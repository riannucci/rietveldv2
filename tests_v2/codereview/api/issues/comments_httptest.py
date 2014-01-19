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

from . import create_issue_httptest

def set_up_single_comment(api):
  with api.cloak():
    iid, xsrf = create_issue_httptest.set_up_single_issue(api)
    api.logout()

  comment = {
    'body': 'I like this line very much.\nMaybe if it had more syllables?',
    'lineno': 2,
    'side': 'old',
  }

  api.POST('issues/%d/patchsets/1/patches/1/drafts' % iid, xsrf=xsrf,
           json=comment)

  api.login()

  api.POST('issues/%d/patchsets/1/patches/1/drafts' % iid, xsrf=xsrf,
           json=comment)

  return iid, xsrf


def Execute(api):
  iid, xsrf = set_up_single_comment(api)

  api.PUT('issues/%d/patchsets/1/patches/1/drafts/1' % iid, xsrf=xsrf, json={
    'body': 'I do not really like this line at all...'
  })

  api.GET('issues/%d/patchsets/1/patches/1/drafts' % iid)

  api.GET('issues/%d/patchsets/1/patches/1/drafts/1' % iid)

  api.DELETE('issues/%d/patchsets/1/patches/1/drafts/1' % iid, xsrf=xsrf)

  api.GET('issues/%d/patchsets/1/patches/1/drafts' % iid)

  api.GET('issues/%d/patchsets/1/patches/1/drafts/1' % iid)
