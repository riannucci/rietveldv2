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
import random

R = random.Random(1337)
DATA = lambda: ''.join(chr(R.randint(0, 255)) for _ in xrange(60 * 1024))
MIMETYPE = 'application/octet-stream'

def Execute(api):
  ex_files = []
  def add_file():
    data = DATA()
    csum = hashlib.sha256(data)
    csum.update(str(len(data)))
    csum.update(MIMETYPE)
    ex_files.append({
      'data': data.encode('base64'),
      'cas_id': {
        'csum': csum.hexdigest(),
        'size': len(data),
        'content_type': MIMETYPE
      }
    })
    return ex_files[-1]['cas_id']

  cas_ids = []
  cas_ids.append(add_file())
  add_file()
  cas_ids.append(add_file())

  fake_id = cas_ids[-1].copy()
  fake_id['csum'] = 'feedface' + fake_id['csum'][8:]
  cas_ids.append(fake_id)

  api.login()
  me = api.GET('accounts/me').json
  api.PUT('cas_entries', json=ex_files, xsrf=me['data']['xsrf'],
          compress=True)

  api.GET('cas_entries/lookup', json=cas_ids)
