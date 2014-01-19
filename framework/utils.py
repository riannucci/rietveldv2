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

import collections
import functools
import logging
import os
import traceback
import urllib

from google.appengine.ext import ndb


def completed_future(value):
  f = ndb.Future('completed_future (%r)' % value)
  f.set_result(value)
  return f


NONE_FUTURE = completed_future(None)


def constant_time_equals(a, b):
  if len(a) != len(b):
    return False
  acc = 0
  for ai, bi in zip(a, b):
    acc |= ord(ai) ^ ord(bi)
  return acc == 0


class LazyLineSplitter(collections.Sequence):
  def __init__(self, data, lineending='\n'):
    self._data = data
    self._offsets = []

    start = 0
    end = None
    while start < len(data):
      end = data.find(lineending, start)
      if end == -1:
        self._offsets.append((start, len(data)))
        break
      self._offsets.append((start, end + len(lineending)))
      start = end + len(lineending)

  @property
  def raw_data(self):
    return self._data

  def __getitem__(self, idx):
    if isinstance(idx, slice):
      return [self._data[b:e] for b, e in self._offsets[idx]]
    else:
      return self._data[slice(*self._offsets[idx])]

  def __len__(self):
    return len(self._offsets)


class IdentitySet(collections.MutableSet):
  # pylint thinks we should implement __getitem__
  # pylint: disable=R0924
  def __init__(self):
    self.data = {}

  def __contains__(self, item):
    return id(item) in self.data

  def __iter__(self):
    return self.data.itervalues()

  def __len__(self):
    return len(self.data)

  def add(self, item):
    self.data[id(item)] = item

  def discard(self, item):
    del self.data[id(item)]


def make_set(obj):
  if not isinstance(obj, (list, tuple, set, frozenset)):
    obj = (obj,)
  return frozenset(obj)


def wsgi_full_url():
  """
  Copied verbatim from:
    http://www.python.org/dev/peps/pep-0333/#url-reconstruction
  """
  url = os.environ['wsgi.url_scheme']+'://'

  if os.environ.get('HTTP_HOST'):
    url += os.environ['HTTP_HOST']
  else:
    url += os.environ['SERVER_NAME']

    if os.environ['wsgi.url_scheme'] == 'https':
      if os.environ['SERVER_PORT'] != '443':
        url += ':' + os.environ['SERVER_PORT']
    else:
      if os.environ['SERVER_PORT'] != '80':
        url += ':' + os.environ['SERVER_PORT']

  url += urllib.quote(os.environ.get('SCRIPT_NAME', ''))
  url += urllib.quote(os.environ.get('PATH_INFO', ''))
  if os.environ.get('QUERY_STRING'):
    url += '?' + os.environ['QUERY_STRING']

  return url


class hybridmethod(object):
  """A decorator for methods which causes them to behave as a hybrid between
  a classmethod and a normal method.

  The decorated method should have the following signature:
    @hybridmethod
    def method((self, cls), ...): ...

  The first argument could also be something like 'self_or_cls', but that's
  just ugly :).

  'self' may be None, if the method was invoked as a classmethod.
  """

  def __init__(self, func):
    self.func = func
    self.__name__ = func.__name__
    self.__doc__ = func.__doc__
    self.__module__ = func.__module__

  def __get__(self, obj, cls):
    @functools.wraps(self.func)
    def wrapped(*args, **kwargs):
      return self.func((obj, cls), *args, **kwargs)
    return wrapped


def deco_with_extra_params(deco):
  """Decorator wrapper to allow a regular decorator to take optional parameters.

  Returns a decorator which may be used as:
    deco = deco_with_extra_params(_deco)

    @deco
    def foo(): ...
    # foo = _deco(foo)

    OR

    @deco(arg1, key=value)
    def foo(): ...
    # foo = _deco(foo, arg1, key=value)
  """
  @functools.wraps(deco)
  def wrapped_decorator(fn_or_not=None, *args, **kwargs):
    if callable(fn_or_not) and not (args or kwargs):
      return deco(fn_or_not)
    else:
      return lambda fn: deco(fn, fn_or_not, *args, **kwargs)
  return wrapped_decorator


class _cached_assignable_property(object):
  """Like @property, except that del and set are pass-through, and the result
  of get is cached on self.{field_name or '_' + fn.__name__}.

  Args:
    field_name (str, optional) - The name of the attribute to cache data on.
      Normally field_name is simply the name of the decorated function prefixed
      with an underscore.

  >>> class Test(object):
  ...  @cached_assignable_property('tarply')
  ...  def foo(self):
  ...   print "hello"
  ...   return 10
  ...
  >>> t = Test()
  >>> t.foo
  hello
  10
  >>> t.foo
  10
  >>> t.tarply  # This is the overridden field_name
  10
  >>> t.foo = 20
  >>> t.foo
  20
  >>> del t.foo
  >>> t.foo
  hello
  10
  >>>
  """
  def __init__(self, fn, field_name=None):
    self.func = fn
    self._iname = field_name or ("_" + fn.__name__)
    self.__name__ = fn.__name__
    self.__doc__ = fn.__doc__
    self.__module__ = fn.__module__

  @staticmethod
  def _last_frames(limit=4, exclude=2):
    """Returns the last |limit+exclude| frames, formated as a normal traceback,
    excluding the top |exclude| frames."""
    stack = traceback.extract_stack(limit=limit+exclude)[:-exclude]
    return ''.join(traceback.format_list(stack))

  def __get__(self, inst, cls=None):
    if inst is None:
      return self
    if not hasattr(inst, self._iname):
      val = self.func(inst)
      # Some methods call out to another layer to calculate the value. This
      # higher layer will assign directly to the property, so we have to do
      # the extra hasattr here to determine if the value has been set as a side
      # effect of func()
      if not hasattr(inst, self._iname):
        setattr(inst, self._iname, val)
    return getattr(inst, self._iname)

  def __set__(self, inst, val):
    if hasattr(inst, self._iname):
      logging.warn("Setting %s.%s more than once.\n%s", inst, self.__name__,
                   self._last_frames(4))
    setattr(inst, self._iname, val)

  def __delete__(self, inst):
    if hasattr(inst, self._iname):
      delattr(inst, self._iname)
cached_assignable_property = deco_with_extra_params(_cached_assignable_property)


class _cached_property(_cached_assignable_property):  # pylint: disable=R0921
  """Same as cached_assignable_property, except that set and del raise
  NotImplementedError.
  """
  def __set__(self, inst, val):
    raise NotImplementedError()

  def __delete__(self, inst):
    raise NotImplementedError()
cached_property = deco_with_extra_params(_cached_property)


class _clearable_cached_property(_cached_assignable_property):  # pylint: disable=R0921
  """Same as cached_assignable_property, except that set raises
  NotImplementedError.
  """
  def __set__(self, inst, val):
    raise NotImplementedError()
clearable_cached_property = deco_with_extra_params(_clearable_cached_property)
