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

def Execute(api):
  api.login()
  api.GET('accounts/me')
  api.GET('accounts/test@example.com')
  api.logout()
  api.GET('accounts/test@example.com')
  api.login('bob@VictoryLane.co.uk', admin=True)
  api.GET('accounts/me')
  api.GET('accounts/test@example.com')
  api.GET('accounts/bob@VictoryLane.co.uk')
  api.GET('accounts/BOB@Victorylane.co.uk')
