# Copyright 2013 Google Inc.
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

import functools
import logging

from google.appengine.ext import ndb

from . import exceptions


def listify(obj):
  if isinstance(obj, basestring):
    return [obj]
  assert isinstance(obj, (tuple, set, list, frozenset))
  return list(obj)


class CASTypeRegistry(dict):
  """A dict with a __call__() decorator method for making |type_map|s for
  |CASEntry.new()|.

  Pro Tip: You can initialize a new CASTypeRegistry from an existing one.
    >>> import default_data_types
    >>> my_reg = CASTypeRegistry(default_data_types.TYPE_MAP)
    >>> my_reg
    {
      (DATA, 'text/plain'): <function ...>,
      (CHARSET, 'utf-8'): <function ...>,
      ...
    }
  """

  DATA = 'data'
  CHARSET = 'charset'

  def __call__(self, *type_names, **kwargs):
    """Use to decorate a validator function for CAS |data_type|s.

    >>> reg = CASTypeRegistry()
    >>> @reg("application/cool_type")
    ... def application_cool_type(data):  # Function name doesn't matter
    ...   return MyCoolType(data)  # Throws if data is unparsable
    ...
    >>> reg['application/cool_type]('\xc0\x01compatible_data')
    [out] <__main__.MyCoolType object at 0x101b7dfd0>
    >>> reg['application/cool_type]('bad data!')
    [log] ERROR:root:While validating ('data', 'application/cool_type')
    [log] Traceback (most recent call last):
    [log]   ... real traceback with implementation details of MyCoolType
    [log] MyCoolTypeException: Data didn't match secret key "\xc0\x01"!
    [out] Traceback (most recent call last):
    [out]   ... Short traceback from CASTypeRegistry ...
    [out] models.CASValidationError: Invalid ('data', 'application/cool_type')
    """
    def inner(f):
      """Wraps f(data) to validate |type_names| MIME types.

      f is expected to raise an exception (the type doesn't matter), if data
      isn't compatible with the indicated |type_names|.

      f may optionally return a 'parsed' form of data which is more natural to
      work with in the python context. If f returns None, the wrapper function
      will return |data| instead, as a convenience.
      """
      charsets = listify(kwargs.pop('charset', ()))
      assert bool(charsets) ^ bool(type_names)
      prefix = self.CHARSET if charsets else self.DATA
      name_list = charsets or type_names
      assert all(x.islower() for x in name_list)
      primary_name = (prefix, name_list[0])

      @functools.wraps(f)
      @ndb.tasklet
      def wrapped(data):
        try:
          ret = f(data)
          if hasattr(ret, 'get_result'):  # It's a futureish
            ret = yield ret
          if ret is None:
            ret = data
        except:
          # Log exception for debugging, but neuter the raised exception to
          # avoid leakage of implementation details.
          logging.exception("While validating %r", primary_name)
          raise exceptions.CASValidationError("Invalid %r" % (primary_name,))
        raise ndb.Return(ret)
      required_charsets = listify(kwargs.get('require_charset', ()))
      wrapped.required_charsets = set(required_charsets)

      for name in name_list:
        self[prefix, name] = wrapped

      return wrapped
    return inner

  @ndb.tasklet
  def validate_async(self, data, data_type, charset=None, check_refs=True):
    if (self.DATA, data_type) not in self:
      raise exceptions.CASUnknownDataType(self, data_type)
    data_type_async = self[self.DATA, data_type]

    if charset:
      if (self.CHARSET, charset) not in self:
        raise exceptions.CASUnknownCharset(self, charset)
      charset_async = self[self.CHARSET, charset]

    required_charsets = data_type_async.required_charsets
    if (required_charsets and charset not in required_charsets):
      raise exceptions.CASCharsetInadequate(data_type, charset,
                                            required_charsets)

    if charset:
      data = yield charset_async(data)
    data = yield data_type_async(data)
    assert data is not None

    if check_refs:
      refs = getattr(data, 'CAS_REFERENCES', ())
      assert None not in (yield [cid.entry_async for cid in refs])

    raise ndb.Return(data)
