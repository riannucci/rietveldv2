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

from django.conf import settings

from google.appengine.ext import ndb
from google.appengine.api import taskqueue

import contextlib
import functools
import datetime
import os
import random
import threading
import time


orig_datetime = datetime.datetime


# ndb is very particular about the types of its datetime stuff. So
# create a little fake inheritance cycle to pass isinstance checks, since
# we're changing the value of datetime.datetime :).
class MetaMockedDatetime(type):
  def __instancecheck__(cls, inst):
    # TODO(iannucci): Support derivatives of MockedDatetime?
    return (inst.__class__ is MockedDatetime or
            isinstance(inst, orig_datetime))


class MockedDatetime(datetime.datetime):
  __metaclass__ = MetaMockedDatetime

  @classmethod
  def utcnow(cls):
    return cls.utcfromtimestamp(time.time())

  @classmethod
  def now(cls):
    return cls.fromtimestamp(time.time())


class MockedTime(object):
  def __init__(self, real_time):
    self._real_time = real_time
    self._local = threading.local()
    self._is_frozen = False

  def seed(self):
    val = os.environ.get('HTTP_X_MOCK_TIME')
    if val is not None:
      self._local.MockedTime_timeval = float(val)

  @contextlib.contextmanager
  def frozen(self):
    old = self._is_frozen
    self._is_frozen = True
    try:
      yield
    finally:
      self._is_frozen = old

  def __call__(self):
    if hasattr(self._local, 'MockedTime_timeval'):
      val = self._local.MockedTime_timeval
      if not self._is_frozen:
        self._local.MockedTime_timeval += 0.1
        os.environ['HTTP_X_MOCK_TIME'] = repr(self._local.MockedTime_timeval)
      return val
    else:
      return self._real_time()


def _time_freezer(fn):
  @functools.wraps(fn)
  def wrapped(*a, **kw):
    with time.time.frozen():
      return fn(*a, **kw)
  return wrapped


class MockedRandomInst(object):
  def __init__(self, real_inst):
    self._real_inst = real_inst
    self._local = threading.local()

  def seed(self):
    self._local.MockedRandomInst_inst = random.Random(time.time())

  def __getattr__(self, name):
    return getattr(self._local.MockedRandomInst_inst, name)


MOCKS_INSTALLED = False
def _install_mocks():
  # pylint: disable=W0212
  global MOCKS_INSTALLED
  if not MOCKS_INSTALLED:
    MOCKS_INSTALLED = True
    time.time = MockedTime(time.time)
    datetime.datetime = MockedDatetime
    mr = MockedRandomInst(random._inst)
    for name in (x for x in dir(random.Random) if x[0] != '_' and x.islower()):
      def wrap(mr, name):
        def wrapped(*a, **kw):
          return getattr(mr, name)(*a, **kw)
        return wrapped
      setattr(random, name, wrap(mr, name))
    random._inst = mr
    os.urandom = lambda n: (
      ''.join(chr(random.randint(0, 255)) for _ in xrange(n))
    )
    taskqueue.taskqueue._PRESERVE_ENVIRONMENT_HEADERS += (
      ('X-Mock-Time', 'HTTP_X_MOCK_TIME'),
    )
    # This goes through proprerties in dict order (random). If multiple
    # properties rely on time, then they'll get randomly ordered time values.
    ndb.Model._prepare_for_put = _time_freezer(ndb.Model._prepare_for_put )
  time.time.seed()
  random._inst.seed()


class DjangoMockMiddleware(object):
  def process_request(self, _request):
    assert settings.DEBUG
    _install_mocks()
