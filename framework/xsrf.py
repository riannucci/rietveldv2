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

from . import exceptions, account, utils

HEADER = 'HTTP_X_XSRF_TOKEN'

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
  raw_request_xsrf = os.environ.get(HEADER, None)

  verified = os.environ.get('XSRF_OK', None)
  if verified is not None:
    verified = int(verified)
    if verified == 1:
      return raw_request_xsrf
    else:
      raise exceptions.Forbidden('make request with invalid XSRF Token')
  os.environ['XSRF_OK'] = str(0)

  if raw_request_xsrf is None:
    raise exceptions.Forbidden('make request without XSRF Token')

  try:
    request_xsrf = json.loads(base64.urlsafe_b64decode(raw_request_xsrf))
  except:
    logging.exception('Died while trying to decode XSRF token: %s',
                      request_xsrf)
    raise exceptions.Forbidden('make request with bad XSRF Token (malformed)')

  stamp, tag = request_xsrf.pop('stamp'), request_xsrf.pop('tag')
  if request_xsrf:
    raise exceptions.Forbidden('make request with bad XSRF Token (extra junk)')

  if not isinstance(stamp, int):
    raise exceptions.Forbidden('make request with bad XSRF Token (malformed)')

  try:
    tag = tag.decode('hex')
  except:
    raise exceptions.Forbidden('make request with bad XSRF Token (malformed)')

  now = time.time()
  if now - stamp > (60 * 60):
    raise exceptions.Forbidden('make request with expired XSRF Token')

  if not utils.constant_time_equals(_generate_token(stamp), tag):
    raise exceptions.Forbidden('make request with invalid XSRF Token')

  os.environ['XSRF_OK'] = str(1)
  return raw_request_xsrf


def make_header_token():
  now = int(time.time())
  tag = _generate_token(now).encode('hex')
  return base64.urlsafe_b64encode(json.dumps({'stamp': now, 'tag': tag},
                                             sort_keys=True))
