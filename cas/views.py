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

"""Views for the Content Addressed Store"""

import django.http

class RequestHandler(object):
  """Kinda-sorta like webapp2.RequestHandler."""
  KNOWN_METHODS = frozenset(('get', 'head', 'post', 'put', 'delete'))

  def __init__(self, request):
    self.request = request

  @classmethod
  def __call__(cls, request, *args, **kwargs):
    fn = getattr(cls, request.method.lower(), None)
    if fn is None:
      ok_methods = [x.upper() for x in dir(cls) if x in cls.KNOWN_METHODS]
      return django.http.HttpResponseNotAllowed(ok_methods)
    inst = cls(request)
    return fn(inst, *args, **kwargs)


class MainHandler(RequestHandler):
  def get(self):
    """Get the status of multiple objects in the CAS."""
    pass

  def post(self):
    """Add multiple objects to the CAS."""
    pass


class EntityHandler(RequestHandler):
  # TODO(iannucci): Implement head() to get status of a single object.

  def get(self, entry_id):
    pass
