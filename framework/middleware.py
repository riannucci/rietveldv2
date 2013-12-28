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


class JSONResponseMiddleware(Middleware):
  @staticmethod
  def _json_default(obj):
    # TODO(iannucci): Make this know about various types
    return str(obj)

  def post(self, request, result):
    result = result or {}
    if isinstance(result, HttpResponse):
      return result
    assert isinstance(result, dict)
    headers = result.pop(HEADERS, {})
    code = result.pop(STATUS_CODE, 200)
    if 'status' not in result:
      status = 'UNKNOWN'
      if 100 <= code < 200:
        status = 'INFO'
      elif 200 <= code < 300:
        status = 'OK'
      elif 300 <= code < 400:
        status = 'REDIRECT'
      elif 400 <= code < 500:
        status = 'ERROR'
      elif 500 <= code:
        status = 'SERVER_ERROR'
      else:
        logging.warn('JSON response code unknown? %s', result)
      result['status'] = {'type': status, 'code': code}
    result = json.dumps(result, separators=(',',':'), sort_keys=True,
                        default=self._json_default)
    ret = HttpResponse(result, content_type='application/json; charset=utf-8',
                       status=code)
    # HttpResponse apparently doesn't have .update()
    for k, v in headers.iteritems():
      ret[k] = v
    return ret

  def error(self, request, ex_info):
    logging.error('Handling JSON error.', exc_info=ex_info)
    status = getattr(ex_info[1], 'STATUS_CODE', 500)
    headers = getattr(ex_info[1], 'HEADERS', {})
    r = {
      STATUS_CODE: status,
      HEADERS: headers,
      'data': {
        'msg': str(ex_info[1]),
      }
    }
    data = getattr(ex_info[1], 'DATA', None)
    if data:
      r['data'].update(data)
    return self.post(request, r)


class MethodOverride(Middleware):
  OK_METHODS = set(('GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS'))

  def pre(self, request, *args, **kwargs):
    request.method = request.META.get('HTTP_X_HTTP_METHOD_OVERRIDE',
                                      request.method)
    assert request.method in self.OK_METHODS
    return args, kwargs
