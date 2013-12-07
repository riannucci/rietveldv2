import json
import logging

from django.http import HttpResponse

from . import exceptions

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


class JSONMiddleware(Middleware):
  STATUS_CODE = object()
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
    code = result.pop(self.STATUS_CODE, 200)
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
      'msg': str(ex_info[1]), self.STATUS_CODE: status})


class MethodOverride(Middleware):
  OK_METHODS = set(('GET', 'POST', 'PUT', 'DELETE', 'HEAD'))

  def pre(self, request, *args, **kwargs):
    method = request.GET.get('_method', None)
    if method is None:
      method = request.META.get('HTTP_X_HTTP_METHOD_OVERRIDE', None)
    request.method = method or request.method
    assert request.method in self.OK_METHODS
    return args, kwargs
