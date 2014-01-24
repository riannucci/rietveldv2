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

def cas_id_str(cas_id):
  ret = '%(csum)s:%(size)d:%(content_type)s' % cas_id
  if 'charset' in cas_id:
    ret += ':%(charset)s' % cas_id
  return ret

def prove(xsrf, data, salt):
  salt = salt.decode('base64')
  salt_hash = hmac.new(salt, data, HASH_ALGO).digest()
  return hmac.new(xsrf, salt_hash, HASH_ALGO).hexdigest()

def entry(data, cas_id):
  return {
    'data': data.encode('base64')[:-1],
    'cas_id': cas_id
  }

FILE_BEFORE = """
Waters move softly
The lake gently beckons you
Slip into it's grave
"""
FILE_BEFORE_CAS_ID = cas_id(FILE_BEFORE)
FILE_BEFORE_ENTRY = entry(FILE_BEFORE, FILE_BEFORE_CAS_ID)

FILE_AFTER = """
Waters move softly
The hat gently pokes you
Put it on your face
"""
FILE_AFTER_CAS_ID = cas_id(FILE_AFTER)
FILE_AFTER_ENTRY = entry(FILE_AFTER, FILE_AFTER_CAS_ID)

PATCHSET = json.dumps({
  'patches': [
    {
      'action': 'rename',
      'old': {
        'data': FILE_BEFORE_CAS_ID,
        'path': 'path/to/haiku.txt', 'mode': 0100644,
        'timestamp': 'fake timestamp'
      },
      'new': {
        'data': FILE_AFTER_CAS_ID,
        'path': 'different/path/to/haiku.txt', 'mode': 0100644,
        'timestamp': 'fake timestamp'
      }
    }
  ]
})
PATCHSET_CAS_ID = cas_id(PATCHSET, 'application/patchset+json')
PATCHSET_CAS_ID_STR = cas_id_str(PATCHSET_CAS_ID)

FILE_AFTER_2 = """
Waters move quickly
The hat gently pokes you
Put it on your face
"""
FILE_AFTER_2_CAS_ID = cas_id(FILE_AFTER_2)

PATCHSET_2 = json.dumps({
  'patches': [
    {
      'action': 'rename',
      'old': {
        'data': FILE_BEFORE_CAS_ID,
        'path': 'path/to/haiku.txt', 'mode': 0100644,
        'timestamp': 'fake timestamp'
      },
      'new': {
        'data': FILE_AFTER_2_CAS_ID,
        'path': 'different/path/to/haiku.txt', 'mode': 0100644,
        'timestamp': 'different fake timestamp'
      }
    }
  ]
})
PATCHSET_2_CAS_ID = cas_id(PATCHSET_2, 'application/patchset+json')
PATCHSET_2_CAS_ID_STR = cas_id_str(PATCHSET_2_CAS_ID)


def set_up_single_issue(api):
  api.login()
  xsrf = api.GET('accounts/me').json['data']['xsrf']

  ents = api.PUT('cas_entries', json=[FILE_BEFORE_ENTRY, FILE_AFTER_ENTRY],
                 xsrf=xsrf, compress=True).json['data']

  api.POST('cas_entries/%s' % PATCHSET_CAS_ID_STR, data=PATCHSET, xsrf=xsrf)

  issue = {
    'patchset': PATCHSET_CAS_ID,
    'proofs': {
      FILE_BEFORE_CAS_ID['csum']: prove(xsrf, FILE_BEFORE, ents[0]['salt']),
      FILE_AFTER_CAS_ID['csum']: prove(xsrf, FILE_AFTER, ents[1]['salt']),
    },

    'reviewers': ['bob@gnarly.com'],
    'description': 'A totes awesome haiku\n\nSeriously, it\'s freaking sweet.',
    'subject': 'A totes awesome haiku',
  }
  iid = api.POST('issues', json=issue, xsrf=xsrf).json['data']['id']

  ent = api.POST('cas_entries/%s' % cas_id_str(FILE_AFTER_2_CAS_ID),
                 data=FILE_AFTER_2, xsrf=xsrf, compress=True).json['data']
  api.POST('cas_entries/%s' % PATCHSET_2_CAS_ID_STR,
           data=PATCHSET_2, xsrf=xsrf, compress=True)

  api.POST(
    'issues/%d/patchsets' % iid,
    json={
      'patchset': PATCHSET_2_CAS_ID,
      'proofs': {
        FILE_BEFORE_CAS_ID['csum']: prove(xsrf, FILE_BEFORE, ents[0]['salt']),
        FILE_AFTER_2_CAS_ID['csum']: prove(xsrf, FILE_AFTER_2, ent['salt'])
      }
    },
    xsrf=xsrf)

  api.GET('issues/%d' % iid)

  api.PUT('issues/%d' % iid, json={'cc': ['coolguy@FancyDomain.co.uk']},
          xsrf=xsrf)

  return iid, xsrf


def Execute(api):
  iid, xsrf = set_up_single_issue(api)

  api.POST('issues',
           json={
             'send_message': True, 'cc': [], 'reviewers': [],
             'patchset': PATCHSET_CAS_ID
           },
           xsrf=xsrf)
  api.comment('Should fail because we cannot send a message without anyone to '
              'send it to')

  api.POST('issues',
           json={
             'cc': [], 'reviewers': [],
             'patchset': PATCHSET_CAS_ID
           },
           xsrf=xsrf)
  api.comment('Should fail because insufficient proof.')

  ents = api.GET('cas_entries/lookup',
                 json=[FILE_BEFORE_CAS_ID, FILE_AFTER_CAS_ID],
                 compress=True).json['data']

  api.GET('issues/%d/patchsets' % iid)

  api.GET('issues/%d/patchsets/1' % iid)

  api.GET('issues/%d/patchsets/1/diff' % iid)

  api.GET('issues/%d/patchsets/1/patches' % iid)

  api.GET('issues/%d/patchsets/1/patches/1' % iid)

  api.GET('issues/%d/patchsets/1/patches/4' % iid)

  api.GET('issues/%d/patchsets/1/patches/1/diff' % iid)

  api.GET('issues/%d/patchsets/1/patches/1/diff/2/1' % iid)

  api.DELETE('issues/%d/patchsets/1' % iid, xsrf=xsrf)

  api.GET('issues/%d/patchsets' % iid)

  api.GET('issues/%d/patchsets/1' % iid)

  api.GET('issues/%d/patchsets/1/patches/1' % iid)

  api.DELETE('issues/%d' % iid, xsrf=xsrf)

  api.GET('issues/%d' % iid)
  api.comment('Deleted issues should yield the same error as issues which '
              'never existed in the first place.')

  api.GET('issues/%d' % (iid + 1))
