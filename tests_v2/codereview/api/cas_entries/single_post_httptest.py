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
DATA = ''.join(chr(R.randint(0, 255)) for _ in xrange(1024))
MIMETYPE = 'application/octet-stream'

def Execute(api):
  csum = hashlib.sha256(DATA)
  csum.update(str(len(DATA)))
  csum.update(MIMETYPE)

  api.login()
  me = api.GET('accounts/me').json

  cas_id = '%s:%s:%s' % (csum.hexdigest(), len(DATA), MIMETYPE)
  api.POST('cas_entries/%s' % cas_id, data=DATA, xsrf=me['data']['xsrf'])
