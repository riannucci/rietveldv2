import sys
import types

from google.appengine.ext import ndb

from django.http import HttpRequest, HttpResponseNotAllowed
from django.core.urlresolvers import resolve

from . import exceptions
from . import middleware
from . import utils


def _is_exc_info(data):
  if not isinstance(data, tuple) or len(data) != 3:
    return False
  return isinstance(data[-1], types.TracebackType)


@utils.cached_property
def _RouteNameResolver(request):
  return resolve(request.path).url_name

@ndb.toplevel
def _process_request(handler_fn, mware, request, *args, **kwargs):
  # Bind a _RouteNameResolver property to this request.
  request.route_name = _RouteNameResolver.__get__(request, HttpRequest)

  args, kwargs = reduce(lambda (a, kw), m: m.pre(request, *a, **kw), mware,
                        (args, kwargs))
  try:
    ret = handler_fn(request, *args, **kwargs)
  except Exception:
    ret = sys.exc_info()

  rslt = ret
  for m in reversed(mware):
    method = m.error if _is_exc_info(rslt) else m.post
    rslt = method(request, rslt)
  if isinstance(rslt, tuple):
    raise rslt[0], rslt[1], rslt[2]
  return rslt


class RequestHandler(object):
  """Kinda-sorta like webapp2.RequestHandler."""
  _KNOWN_METHODS = frozenset(('get', 'head', 'post', 'put', 'delete'))

  TRANSACTIONAL = ndb.trasactional

  def __init__(self):
    self.OK_METHODS = [x.upper() for x in dir(self) if x in self._KNOWN_METHODS]

    # Your subclass should set these
    self.MIDDLEWARE = [] # list of Middleware instances.
    self.ROUTES = {}  # {<route_name:str>: <regex:str>}

  @ndb.toplevel
  def __call__(self, request, *args, **kwargs):
    handler_fn = getattr(self, request.method.lower(), None)
    if handler_fn is None:
      return HttpResponseNotAllowed(self.OK_METHODS)

    processor = _process_request
    if self.TRANSACTIONAL:
      processor = self.TRANSACTIONAL(processor)

    return processor(handler_fn, self.MIDDLEWARE, request, *args, **kwargs)


class _RESTHandler(RequestHandler):
  PARENT = None
  PREFIX = None

  @utils.cached_property
  def url_token(self):
    raise NotImplementedError()

  def __init__(self, *args, **kwargs):
    super(_RESTHandler, self).__init__(*args, **kwargs)
    assert bool(self.PARENT) ^ bool(self.PREFIX)

    if self.PARENT:
      self.MIDDLEWARE = self.PARENT.MIDDLEWARE + self.MIDDLEWARE
    else:
      self.MIDDLEWARE = [middleware.MethodOverride,
                         middleware.JSONMiddleware]
      self.PREFIX = self.PARENT.PREFIX+self.url_token+'/'

    self.name = self.__class__.__name__
    self.ROUTES = {self.name.lower()+'_api': self.PREFIX+'?$'}


class RESTCollectionHandler(_RESTHandler):
  @utils.cached_property
  def url_token(self):
    return self.name.lower()

  # make put() do a multi-post


class RestItemMilddeware(middleware.Middleware):
  def __init__(self, name):
    self.name = name

  def pre(self, request, id, *args, **kwargs):
    parent_keys = getattr(request, 'entity_keys', None)
    if parent_keys is None:
      key = ndb.Key(self.name, id)
      request.entity_keys = [key]
    else:
      key = ndb.Key(pairs=parent_keys[-1].pairs() + (self.name, id))
      request.entity_keys.append(key)

    setattr(request, '%s_key' % self.name, key)

    fut = key.get_async()

    @ndb.tasklet
    def get_entity_async():
      data = yield fut
      if data is None:
        raise exceptions.NotFound("%s '%s'" % (self.name, id))
      raise ndb.Return(data)
    get_entity_async.__name__ = '%s_async' % self.name
    setattr(request, '%s_async' % self.name, get_entity_async)

    return args, kwargs


class RESTItemHandler(_RESTHandler):
  # pylint thinks that nothing uses this abstract class, but it's wrong.
  # pylint: disable=R0921

  def __init__(self, *args, **kwargs):
    super(RESTItemHandler, self).__init__(*args, **kwargs)
    self.MIDDLEWARE = self.MIDDLEWARE + [RestItemMilddeware(self.name)]

  @utils.cached_property
  def url_token(self):
    return r'(\d+)'
