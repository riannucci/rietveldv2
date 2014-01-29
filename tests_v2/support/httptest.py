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

import contextlib
import functools
import gzip
import json
import os
import random
import re
import signal
import subprocess
import sys

from cStringIO import StringIO

import requests

from . import test

from ..main import get_tmp_dir, SRC_ROOT_PATH, import_helper


def scrub(obj, replacements):
  if isinstance(obj, basestring):
    for pattern, replacement in replacements:
      obj = pattern.sub(replacement, obj)
    return obj
  elif isinstance(obj, (list, tuple, set, frozenset)):
    return obj.__class__(scrub(o, replacements) for o in obj)
  elif isinstance(obj, dict):
    return {k: scrub(v, replacements) for k, v in obj.iteritems()}
  return obj


class EasyResponse(object):
  STANDARD_REPLACEMENTS = (
    (re.compile(r'localhost(:|%3[aA])\d+'), r'SERVER\1PORT'),
  )

  def __init__(self, method, resource, response):
    """
    @type response: requests.Response
    """
    self.method = method
    self.resource = resource

    self.response = response
    self.code = response.status_code
    try:
      self.json = scrub(response.json(), self.STANDARD_REPLACEMENTS)
    except ValueError:
      self.json = None
    self.raw = scrub(response.text.splitlines(), self.STANDARD_REPLACEMENTS)

  def to_dict(self):
    ret = {
      'request': '%s %r' % (self.method, str(self.resource)),
      'response': {
        'code': self.code,
      }
    }
    if self.json:
      ret['response']['json'] = self.json
    else:
      ret['response']['raw'] = self.raw
    return ret


class HttpTestApi(object):
  def __init__(self, name, base_url, resource_prefix):
    self._base_url = base_url

    self.auto_kwargs = {
      'timeout': 10.0
    }
    self.state = []

    self._cloaked = False

    self._resource_prefix = None
    self._session = requests.Session()

    self.resource_prefix = resource_prefix

    self._timeval = 1388110998.37947
    self._timerng = random.Random(name)

  def add_time(self, amount=None):
    if amount is None:
      amount = (self._timerng.random() * 59) + 1
    assert isinstance(amount, float) and amount > 0
    self._timeval += amount

  @property
  def resource_prefix(self):
    return self._resource_prefix

  @resource_prefix.setter
  def resource_prefix(self, val):
    if val[0] == '/':
      val = val[1:]
    self._resource_prefix = val

  def request(self, method, resource, **kwargs):
    resource_prefix = kwargs.pop('resource_prefix', self.resource_prefix)

    # Make sure we only have one of the keys which results in body data.
    has = lambda key: int(key in kwargs)
    assert sum(map(has, ['data', 'files', 'json'])) <= 1

    k = self.auto_kwargs.copy()
    k.setdefault('allow_redirects', False)
    k.update(kwargs)
    k.setdefault('headers', {})['X-Mock-Time'] = repr(self._timeval)
    xsrf = k.pop('xsrf', None)
    if xsrf:
      k['headers']['X-XSRF-Token'] = xsrf
    self.add_time()

    j = k.pop('json', None)
    if j is not None:
      if method in ('GET', 'HEAD'):
        # trim trailing \n
        if k.pop('compress', False):
          param = StringIO()
          gfile = gzip.GzipFile(fileobj=param, mode='w', mtime=0)
          json.dump(j, gfile, sort_keys=True)
          gfile.close()
          k['params'] = {
            'json': param.getvalue().encode('base64')[:-1],
            'compress': 1
          }
        else:
          k['params'] = {'json': json.dumps(j).encode('base64')[:-1]}
      else:
        k['headers']['Content-Type'] = 'application/json'
        k['data'] = json.dumps(j)

    if k.pop('compress', False):
      assert 'data' in k
      k['headers']['Content-Encoding'] = 'gzip'
      body = StringIO()
      gfile = gzip.GzipFile(fileobj=body, mode='w')
      gfile.write(k['data'])
      gfile.close()
      k['data'] = body.getvalue()

    uri = '/'.join(filter(bool, (self._base_url, resource_prefix, resource)))
    try:
      r = self._session.request(method, uri, **k)
    except requests.exceptions.Timeout as to:
      raise Exception(str(to))
    final_resource = r.url[len(self._base_url)+1:]
    r = EasyResponse(method, final_resource, r)

    if not self._cloaked:
      self.state.append(r.to_dict())

    return r

  # http://localhost:8080/_ah/login?email=test@example.com&action=Login
  def login(self, user='test@example.com', admin=False):
    self.GET('_ah/login', resource_prefix='', params={
      'action': 'Login', 'admin': str(admin), 'email': user})

  def logout(self):
    self.GET('_ah/login', resource_prefix='', params={'action': 'Logout'})
    # There's a bit of a bug when getting set-cookie= statements from localhost
    # so help things along a bit.
    del self._session.cookies['dev_appserver_login']

  def comment(self, comment):
    assert not self._cloaked, "Cannot comment while cloaked!"
    self.state[-1]['response'].setdefault('comments', []).append(comment)

  @contextlib.contextmanager
  def cloak(self, newval=True):
    oldval = self._cloaked
    try:
      self._cloaked = newval
      yield
    finally:
      self._cloaked = oldval

  METHODS = ('POST', 'PUT', 'GET', 'DELETE', 'HEAD', 'OPTIONS')
  def __getattr__(self, attr):
    if attr in self.METHODS:
      return functools.partial(self.request, attr)
    return super(HttpTestApi, self).__getattribute__(attr)


class HttpTest(test.Test):
  def __init__(self, mod_name, infile, resource_prefix):
    self.mod_name = mod_name
    self.infile = infile
    self.resource_prefix = resource_prefix

    self.service = None
    self.service_url = None
    self.admin_url = None

    name, expect = test.test_file_to_name_expectation(infile)

    super(HttpTest, self).__init__(None, name, expect)

    self.storage_path = os.path.join(get_tmp_dir(), 'http_test', self.name)
    os.makedirs(self.storage_path)

  def _start_server(self):
    # Another (possibly better) approach would be generating a fixed port
    # schema based on which multiprocess Process is actually executing the test.
    assert not self.service
    self.service = subprocess.Popen(
      [
        'dev_appserver.py',
        '--storage_path', self.storage_path,
        '--port=0',
        '--admin_port=0',
        SRC_ROOT_PATH
      ],
      stderr=subprocess.PIPE,
      stdout=sys.stdout,
    )

    while True:
      line = self.service.stderr.readline()
      if 'Starting admin server at:' in line:
        assert not self.admin_url
        self.admin_url = line[line.index('http'):].strip().strip('/')
      elif 'Starting module "default" running at:' in line:
        # TODO(iannucci): support arbitrary modules/backends
        assert not self.service_url
        self.service_url = line[line.index('http'):].strip().strip('/')

      if self.admin_url and self.service_url:
        break

  def _stop_server(self):
    self.service.send_signal(signal.SIGTERM)
    self.service.wait()

  def _execute(self):
    self._start_server()
    try:
      func = __import__(self.mod_name, fromlist=['Execute']).Execute
      api = HttpTestApi(self.name, self.service_url, self.resource_prefix)
      func(api)
      return api.state
    finally:
      self._stop_server()


def LoadAll(path, resource_prefix=''):
  for m, infile in import_helper(path, 'httptest', ['Execute']):
    yield HttpTest(m.__name__, infile, resource_prefix)
