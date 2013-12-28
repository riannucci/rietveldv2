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

import sys
import types

from google.appengine.ext import ndb

from django.core.urlresolvers import resolve

from . import exceptions

class RequestHandler(object):
  """Kinda-sorta like webapp2.RequestHandler."""
  _KNOWN_METHODS = frozenset(('get', 'head', 'post', 'put', 'delete',
                              'options'))

  def __init__(self, middleware=(), **methods):
    assert set(methods.iterkeys()).issubset(self._KNOWN_METHODS)
    for method_name, func in methods.iteritems():
      assert callable(func)
      setattr(self, method_name, func)

    self.OK_METHODS = [x.upper() for x in dir(self) if x in self._KNOWN_METHODS]

    # Your subclass should set these
    self.MIDDLEWARE = middleware # list of Middleware instances.

  @staticmethod
  def _is_exc_info(data):
    if not isinstance(data, tuple) or len(data) != 3:
      return False
    return isinstance(data[-1], types.TracebackType)

  @ndb.toplevel
  def __call__(self, request, *args, **kwargs):
    request.route_name = lambda: resolve(request.path).url_name
    mware = self.MIDDLEWARE
    args, kwargs = reduce(lambda (a, kw), m: m.pre(request, *a, **kw), mware,
                          (args, kwargs))

    handler_fn = getattr(self, request.method.lower(), None)
    try:
      if handler_fn is None:
        raise exceptions.NotAllowed(request.method, self.OK_METHODS)
      ret = handler_fn(request, *args, **kwargs)
    except Exception:
      ret = sys.exc_info()

    rslt = ret
    for m in reversed(mware):
      method = m.error if self._is_exc_info(rslt) else m.post
      rslt = method(request, rslt)
    if isinstance(rslt, tuple):
      raise rslt[0], rslt[1], rslt[2]
    return rslt
