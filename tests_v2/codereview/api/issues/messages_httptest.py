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

from . import comments_httptest

def Execute(api):
  with api.cloak():
    iid, xsrf = comments_httptest.set_up_single_comment(api)

  api.POST('issues/%d/patchsets/1/patches/1/drafts' % iid, xsrf=xsrf, json={
    'body': 'This line is kinda meh though',
    'lineno': 1,
    'side': 'new',
  })

  api.GET('issues/%d/drafts' % iid)

  api.GET('issues/%d/drafts/2' % iid)

  api.POST('issues/%d/messages' % iid, xsrf=xsrf, json={
    'lead_text': 'Some really helpful comments!',
    'subject': '1337 Code Review',
  })

  api.logout()
  api.login('bob@gnarly.com')

  gnarly_xsrf = api.GET('accounts/me').json['data']['xsrf']
  api.POST('issues/%d/messages' % iid, xsrf=gnarly_xsrf, json={
    'lead_text': 'LgTm!',
  })

  api.GET('issues/%d/patchsets/1/comments' % iid)

  api.GET('issues/%d/patchsets/1/comments/2' % iid)

  # TODO(iannucci): Add test for 'next_with_comment', etc. Requires a
  # multi-patch Patchset.

  api.GET('issues/%d/patchsets/1/patches/1/comments' % iid)

  api.GET('issues/%d/patchsets/1/patches/1/comments/1' % iid)

  api.GET('issues/%d/messages' % iid)

  api.GET('issues/%d/messages/1' % iid)

  # TODO(iannucci): Should the to_dict recursion be limited? i.e. should we see
  # the comments in the patchset and/or the reply_message? Right now we see
  # everything. Maybe we should just get the raw id's back?
  api.POST('issues/%d/messages' % iid, xsrf=gnarly_xsrf, json={
    'lead_text': 'No comments in this one',
    'subject': 'A totes-fine message',
    'patchset_id': 1
  })

  api.POST('issues/%d/messages' % iid, xsrf=gnarly_xsrf, json={
    'lead_text': 'This is a reply',
    # TODO(iannucci): should the RE: be automatic?
    'subject': 'RE: 1337 Code Review',
    'reply_message_id': 1
  })
