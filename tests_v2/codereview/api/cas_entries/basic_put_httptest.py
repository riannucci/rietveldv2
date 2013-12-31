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

DATA = 'I am the fluffiest of bunnies'
MIMETYPE = 'text/plain'
CHARSET = 'utf-8'

def Execute(api):
  csum = hashlib.sha256(DATA)
  csum.update(str(len(DATA)))
  csum.update(MIMETYPE)
  csum.update(CHARSET)
  ex_files = {csum.hexdigest(): (DATA, MIMETYPE, CHARSET)}

  api.PUT('cas_entries', ex_files=ex_files)  # fails b/c logged out
  api.login()
  api.PUT('cas_entries', ex_files=ex_files)  # fails b/c no xsrf
  me = api.GET('accounts/me').json
  api.PUT('cas_entries', ex_files=ex_files, xsrf=me['data']['xsrf'])
