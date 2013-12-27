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

import base64
import hashlib
import hmac
import json
import logging
import os
import time

from google.appengine.ext import ndb
from google.appengine.api import users

from . import exceptions, account

HEADER = 'X-Codereview-XSRF-Token'

class GlobalXSRFSecret(ndb.Model):
  data = ndb.BlobProperty()


_secret = None


@ndb.non_transactional
@ndb.tasklet
def _global_secret():
  # NOTE: This is technically racy, but since the secret is None clause
  # should only ever happen once ever per instance, it should be fine.
  global _secret
  if _secret is None:
    secret = GlobalXSRFSecret.get_by_id(1)
    if secret is None:
      secret = GlobalXSRFSecret(id=1, data=os.urandom(32))
      secret.put()
    _secret = secret.data
  return _secret


def _constant_time_equals(a, b):
  if len(a) != len(b):
    return False
  acc = 0
  for ai, bi in zip(a, b):
    acc |= ord(ai) ^ ord(bi)
  return acc == 0


def _generate_token(stamp):
  user = account.get_current_user()
  assert isinstance(user, users.User)
  assert isinstance(stamp, int) and stamp
  uid = user.user_id()
  assert uid is not None
  h = hmac.new(_global_secret().get_result(), str(stamp), hashlib.sha256)
  h.update(uid)
  return h.digest()


def assert_xsrf():
  request_xsrf = os.environ.get(HEADER, None)
  if request_xsrf is None:
    raise exceptions.Forbidden('Request requires XSRF token.')

  try:
    request_xsrf = json.loads(base64.urlsafe_b64decode(request_xsrf))
  except:
    logging.exception('Died while trying to decode XSRF token: %s',
                      request_xsrf)
    raise exceptions.Forbidden('Malformed XSRF token')

  stamp, tag = request_xsrf.pop('stamp'), request_xsrf.pop('tag')
  if request_xsrf:
    raise exceptions.Forbidden('Extra junk in XSRF header')

  if not isinstance(stamp, int):
    raise exceptions.Forbidden('Malformed XSRF token')

  try:
    tag = tag.decode('hex')
  except:
    raise exceptions.Forbidden('Malformed XSRF token')

  now = time.time()
  if now - stamp > (60 * 60):
    raise exceptions.Forbidden('Expired XSRF token')

  if not _constant_time_equals(_generate_token(stamp), tag):
    raise exceptions.Forbidden('Invalid XSRF token')


def make_header_token():
  now = int(time.time())
  tag = _generate_token(now).encode('hex')
  return base64.urlsafe_b64encode(json.dumps({'stamp': now, 'tag': tag},
                                             sort_keys=True))
