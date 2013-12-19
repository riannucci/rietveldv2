import json
import logging

from google.appengine.ext import ndb

from django.http import HttpResponse

from . import exceptions


STATUS_CODE = object()
HEADERS = object()


class Middleware(object):
  def pre(self, request, *args, **kwargs):  # pylint: disable=W0613
    """Override to pre-process request and/or *args/**kwargs.

    Returns new (args, kwargs). Expected to modify request as a side-effect.
    """
    return args, kwargs

  def post(self, request, result):  # pylint: disable=W0613
    """Override to post-process the return value of a RequestHandler method.

    Returns new result.
    """
    return result

  def error(self, request, exc_info):  # pylint: disable=W0613
    """Override to post-process the exception of a RequestHandler method.

    Returns result or an exception.
    """
    return exc_info


class AsyncEntityLoader(Middleware):
  def __init__(self, name, parent=False):
    """
    Args:
      parent (bool or str) - If True, use the previously loaded entity
        as a parent. If a str, use the previously loaded entity of kind
        |parent|. If False, do not use a parent.
    """
    self.name = name
    self.parent = parent

  def pre(self, request, id, *args, **kwargs):
    # Set request.entity_keys = [] if it's not there.
    request.entity_keys = getattr(request, 'entity_keys', [])

    if self.parent:
      parent = None
      if self.parent == True:
        parent = request.entity_keys[-1]
      elif isinstance(self.parent, basestring):
        parent = next(
          (k for k in request.entity_keys if k.kind() == self.parent),
          None
        )
      assert parent is not None
      key = ndb.Key(flat=parent.flat()+(self.name, id))
    else:
      key = ndb.Key(self.name, id)

    fut = key.get_async()

    @ndb.tasklet
    def get_entity_async():
      data = yield fut
      if data is None:
        raise exceptions.NotFound("%s '%s'" % (self.name, id))
      raise ndb.Return(data)
    get_entity_async.__name__ = '%s_async' % self.name

    request.entity_keys.append(key)
    setattr(request, '%s_key' % self.name, key)
    setattr(request, '%s_async' % self.name, get_entity_async)

    return args, kwargs


class Jinja(Middleware):
  def __init__(self, jinja_env, global_ctx_obj=None):
    """
    @type jinja_env: jinja2.Environment
    """
    self.jinja_env = jinja_env
    self.global_ctx_obj = global_ctx_obj

  def post(self, request, (template, context)):
    status = context.pop(STATUS_CODE, 200)
    headers = context.pop(HEADERS, {})
    if self.global_ctx_obj:
      context.setdefault('global', self.global_ctx_obj(request))

    t = self.jinja_env.get_template(template)
    response = HttpResponse(t.generate(context), status=status)

    for k, v in headers.iteritems():
      response[k] = v

    return response


class JSONMiddleware(Middleware):
  TEMPLATE = object()

  def pre(self, request, *args, **kwargs):
    # TODO(iannucci): Should this check content-type?
    if not hasattr(request, 'json'):
      # Use file-like-interface of HttpRequest
      request.json = json.load(request)
    return args, kwargs

  def post(self, request, result):
    result = result or {}
    assert isinstance(result, dict)
    code = result.pop(STATUS_CODE, 200)
    if 'status' not in result:
      status = 'UNKNOWN'
      if 100 <= code < 200:
        logging.warn('JSON response code informational? %s', result)
        status = 'INFO'
      elif 200 <= code < 300:
        status = 'OK'
      elif 300 <= code < 400:
        logging.warn('JSON response code redirect? %s', result)
        status = 'REDIRECT'
      elif 400 <= code < 500:
        status = 'ERROR'
      elif 500 <= code:
        status = 'SERVER_ERROR'
      result['status'] = status
    result = json.dumps(result, separators=(',',':'), sort_keys=True)
    return HttpResponse(result, content_type='application/json; charset=utf-8',
                        status=code)


  def error(self, request, ex_info):
    if isinstance(ex_info[1], exceptions.SpecialActionException):
      return ex_info
    logging.error("Caught in json_status_response")
    status = getattr(ex_info[1], 'STATUS_CODE', 500)
    return self.post(request, {
      'msg': str(ex_info[1]), STATUS_CODE: status})


class MethodOverride(Middleware):
  OK_METHODS = set(('GET', 'POST', 'PUT', 'DELETE', 'HEAD'))

  def pre(self, request, *args, **kwargs):
    method = request.GET.get('_method', None)
    if method is None:
      method = request.META.get('HTTP_X_HTTP_METHOD_OVERRIDE', None)
    request.method = method or request.method
    assert request.method in self.OK_METHODS
    return args, kwargs
