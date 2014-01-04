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

import hashlib
import json
import hmac

HASH_ALGO = hashlib.sha256

def cas_id(data, content_type='text/plain', charset='utf-8'):
  csum = HASH_ALGO(data)
  csum.update(str(len(data)))
  csum.update(content_type)
  if charset is not None:
    csum.update(charset)
  r = {
    'csum': csum.hexdigest(),
    'size': len(data),
    'content_type': content_type,
  }
  if charset is not None:
    r['charset'] = charset
  return r

def prove(xsrf, data, salt):
  salt = salt.decode('base64')
  salt_hash = hmac.new(salt, data, HASH_ALGO).digest()
  return hmac.new(xsrf, salt_hash, HASH_ALGO).hexdigest()

FILE_BEFORE = """
Waters move softly
The lake gently beckons you
Slip into it's grave
"""
FILE_BEFORE_CAS_ID = cas_id(FILE_BEFORE)

FILE_AFTER = """
Cloth moves softly
The hat gently pokes you
Put it on your face
"""
FILE_AFTER_CAS_ID = cas_id(FILE_AFTER)

def Execute(api):
  api.login()
  xsrf = api.GET('accounts/me').json['data']['xsrf']

  patchset = json.dumps({
    'patches': [
      {
        'action': 'rename',
        'old': {
          'data': FILE_BEFORE_CAS_ID,
          'path': '/path/to/haiku.txt', 'mode': 100644,
          'timestamp': 'fake timestamp'
        },
        'new': {
          'data': FILE_AFTER_CAS_ID,
          'path': '/different/path/to/haiku.txt', 'mode': 100644,
          'timestamp': 'fake timestamp'
        }
      }
    ]
  })
  pset_id = cas_id(patchset, 'application/patchset+json')

  entries = [
    {
      'data': FILE_BEFORE.encode('base64')[:-1],
      'cas_id': FILE_BEFORE_CAS_ID
    },
    {
      'data': FILE_AFTER.encode('base64')[:-1],
      'cas_id': FILE_AFTER_CAS_ID
    }
  ]
  rsp = api.PUT('cas_entries', json=entries, xsrf=xsrf, compress=True)
  ents = rsp.json['data']

  entries = [
    {
      'data': patchset.encode('base64')[:-1],
      'cas_id': pset_id
    }
  ]
  api.POST((
    'cas_entries/%s:%s:%s:utf-8'
    % (pset_id['csum'], pset_id['size'], pset_id['content_type'])),
    data=patchset, xsrf=xsrf)

  issue = {
    'patchset': pset_id,
    'proofs': {
      FILE_BEFORE_CAS_ID['csum']: prove(xsrf, FILE_BEFORE, ents[0]['salt']),
      FILE_AFTER_CAS_ID['csum']: prove(xsrf, FILE_AFTER, ents[1]['salt']),
    },

    'reviewers': ['bob@gnarly.com'],
    'description': 'A totes awesome haiku\n\nSeriously, it\'s freaking sweet.',
    'subject': 'A totes awesome haiku',
  }
  iid = api.POST('issues', json=issue, xsrf=xsrf).json['data']['id']

  api.GET('issues/%d' % iid)

  api.GET('issues/%d/patchsets' % iid)

  api.GET('issues/%d/patchsets/1' % iid)
