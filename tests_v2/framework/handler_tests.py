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

def NotAllowed():
  from framework import handler, exceptions
  from django.http import HttpRequest

  class TestHandler(handler.RequestHandler):
    def get(self, request):
      pass

    def put(self, request):
      pass
  th = TestHandler()

  try:
    req = HttpRequest()
    req.method = 'POST'
    th(req)
  except exceptions.NotAllowed as e:
    return {
      'exeption': repr(e),
      'data': e.DATA,
    }


def GenTests():
  from tests_v2.support.test import BasicTest
  yield BasicTest(NotAllowed)

