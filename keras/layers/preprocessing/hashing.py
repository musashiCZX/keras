# Copyright 2020 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Keras hashing preprocessing layer."""

import tensorflow.compat.v2 as tf
# pylint: disable=g-classes-have-attributes

import functools
import numpy as np
from keras.engine import base_layer
from keras.engine import base_preprocessing_layer
from tensorflow.python.util.tf_export import keras_export


@keras_export('keras.layers.Hashing',
              'keras.layers.experimental.preprocessing.Hashing')
class Hashing(base_layer.Layer):
  """Implements categorical feature hashing, also known as "hashing trick".

  This layer transforms single or multiple categorical inputs to hashed output.
  It converts a sequence of int or string to a sequence of int. The stable hash
  function uses `tensorflow::ops::Fingerprint` to produce the same output
  consistently across all platforms.

  This layer uses [FarmHash64](https://github.com/google/farmhash) by default,
  which provides a consistent hashed output across different platforms and is
  stable across invocations, regardless of device and context, by mixing the
  input bits thoroughly.

  If you want to obfuscate the hashed output, you can also pass a random `salt`
  argument in the constructor. In that case, the layer will use the
  [SipHash64](https://github.com/google/highwayhash) hash function, with
  the `salt` value serving as additional input to the hash function.

  **Example (FarmHash64)**

  >>> layer = tf.keras.layers.Hashing(num_bins=3)
  >>> inp = [['A'], ['B'], ['C'], ['D'], ['E']]
  >>> layer(inp)
  <tf.Tensor: shape=(5, 1), dtype=int64, numpy=
    array([[1],
           [0],
           [1],
           [1],
           [2]])>

  **Example (FarmHash64) with a mask value**

  >>> layer = tf.keras.layers.Hashing(num_bins=3, mask_value='')
  >>> inp = [['A'], ['B'], [''], ['C'], ['D']]
  >>> layer(inp)
  <tf.Tensor: shape=(5, 1), dtype=int64, numpy=
    array([[1],
           [1],
           [0],
           [2],
           [2]])>

  **Example (SipHash64)**

  >>> layer = tf.keras.layers.Hashing(num_bins=3, salt=[133, 137])
  >>> inp = [['A'], ['B'], ['C'], ['D'], ['E']]
  >>> layer(inp)
  <tf.Tensor: shape=(5, 1), dtype=int64, numpy=
    array([[1],
           [2],
           [1],
           [0],
           [2]])>

  **Example (Siphash64 with a single integer, same as `salt=[133, 133]`)**

  >>> layer = tf.keras.layers.Hashing(num_bins=3, salt=133)
  >>> inp = [['A'], ['B'], ['C'], ['D'], ['E']]
  >>> layer(inp)
  <tf.Tensor: shape=(5, 1), dtype=int64, numpy=
    array([[0],
           [0],
           [2],
           [1],
           [0]])>

  Args:
    num_bins: Number of hash bins. Note that this includes the `mask_value` bin,
      so the effective number of bins is `(num_bins - 1)` if `mask_value` is
      set.
    mask_value: A value that represents masked inputs, which are mapped to
      index 0. Defaults to None, meaning no mask term will be added and the
      hashing will start at index 0.
    salt: A single unsigned integer or None.
      If passed, the hash function used will be SipHash64, with these values
      used as an additional input (known as a "salt" in cryptography).
      These should be non-zero. Defaults to `None` (in that
      case, the FarmHash64 hash function is used). It also supports
      tuple/list of 2 unsigned integer numbers, see reference paper for details.
    **kwargs: Keyword arguments to construct a layer.

  Input shape:
    A single or list of string, int32 or int64 `Tensor`,
    `SparseTensor` or `RaggedTensor` of shape `(batch_size, ...,)`

  Output shape:
    An int64 `Tensor`, `SparseTensor` or `RaggedTensor` of shape
    `(batch_size, ...)`. If any input is `RaggedTensor` then output is
    `RaggedTensor`, otherwise if any input is `SparseTensor` then output is
    `SparseTensor`, otherwise the output is `Tensor`.

  Reference:
    - [SipHash with salt](https://www.131002.net/siphash/siphash.pdf)

  """

  def __init__(self, num_bins, mask_value=None, salt=None, **kwargs):
    if num_bins is None or num_bins <= 0:
      raise ValueError(
          f'The `num_bins` for `Hashing` cannot be `None` or non-positive '
          f'values. Received: num_bins={num_bins}.')
    super().__init__(**kwargs)
    base_preprocessing_layer.keras_kpl_gauge.get_cell('Hashing').set(True)
    self.num_bins = num_bins
    self.mask_value = mask_value
    self.strong_hash = True if salt is not None else False
    self.salt = None
    if salt is not None:
      if isinstance(salt, (tuple, list)) and len(salt) == 2:
        self.salt = salt
      elif isinstance(salt, int):
        self.salt = [salt, salt]
      else:
        raise ValueError(
            f'The `salt` argument for `Hashing` can only be a tuple of size 2 '
            f'integers, or a single integer. Received: salt={salt}.')

  def call(self, inputs):
    if isinstance(inputs, (list, tuple, np.ndarray)):
      inputs = tf.convert_to_tensor(inputs)
    if isinstance(inputs, tf.SparseTensor):
      return tf.SparseTensor(
          indices=inputs.indices,
          values=self._hash_values_to_bins(inputs.values),
          dense_shape=inputs.dense_shape)
    return self._hash_values_to_bins(inputs)

  def _hash_values_to_bins(self, values):
    """Converts a non-sparse tensor of values to bin indices."""
    str_to_hash_bucket = self._get_string_to_hash_bucket_fn()
    num_available_bins = self.num_bins
    mask = None
    # If mask_value is set, the zeroth bin is reserved for it.
    if self.mask_value is not None and num_available_bins > 1:
      num_available_bins -= 1
      mask = tf.equal(values, self.mask_value)
    # Convert all values to strings before hashing.
    if values.dtype.is_integer:
      values = tf.as_string(values)
    values = str_to_hash_bucket(values, num_available_bins, name='hash')
    if mask is not None:
      values = tf.add(values, tf.ones_like(values))
      values = tf.where(mask, tf.zeros_like(values), values)
    return values

  def _get_string_to_hash_bucket_fn(self):
    """Returns the string_to_hash_bucket op to use based on `hasher_key`."""
    # string_to_hash_bucket_fast uses FarmHash64 as hash function.
    if not self.strong_hash:
      return tf.strings.to_hash_bucket_fast
    # string_to_hash_bucket_strong uses SipHash64 as hash function.
    else:
      return functools.partial(
          tf.strings.to_hash_bucket_strong, key=self.salt)

  def compute_output_shape(self, input_shape):
    return input_shape

  def compute_output_signature(self, input_spec):
    output_shape = self.compute_output_shape(input_spec.shape)
    output_dtype = tf.int64
    if isinstance(input_spec, tf.SparseTensorSpec):
      return tf.SparseTensorSpec(
          shape=output_shape, dtype=output_dtype)
    else:
      return tf.TensorSpec(shape=output_shape, dtype=output_dtype)

  def get_config(self):
    config = super().get_config()
    config.update({
        'num_bins': self.num_bins,
        'salt': self.salt,
        'mask_value': self.mask_value,
    })
    return config
