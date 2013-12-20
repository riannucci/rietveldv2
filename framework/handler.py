import sys
import types

from google.appengine.ext import ndb

from django.http import HttpResponseNotAllowed
from django.core.urlresolvers import resolve

class RequestHandler(object):
  """Kinda-sorta like webapp2.RequestHandler."""
  _KNOWN_METHODS = frozenset(('get', 'head', 'post', 'put', 'delete'))

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

  def _process_request(self, handler_fn, request, *args, **kwargs):
    request.route_name = lambda: resolve(request.path).url_name

    mware = self.MIDDLEWARE
    args, kwargs = reduce(lambda (a, kw), m: m.pre(request, *a, **kw), mware,
                          (args, kwargs))
    try:
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

  @ndb.toplevel
  def __call__(self, request, *args, **kwargs):
    handler_fn = getattr(self, request.method.lower(), None)
    if handler_fn is None:
      return HttpResponseNotAllowed(self.OK_METHODS)

    return self._process_request(
      handler_fn, self.MIDDLEWARE, request, *args, **kwargs)
