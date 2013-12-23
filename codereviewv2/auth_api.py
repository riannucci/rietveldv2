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

from google.appengine.ext import ndb

from framework import rest_handler, exceptions

from . import auth_models
from .common import API_PREFIX

class Accounts(rest_handler.RESTCollectionHandler):
  PREFIX = API_PREFIX
  MODEL_NAME = 'Account'
  ID_TYPE = str
  ID_TYPE_TOKEN = r'[^@/]+@[^@/]+'
  SPECIAL_ROUTES = {'me': 'me'}

  @ndb.tasklet
  def get_one_async(self, key):
    # Require the user to at least be logged in.
    me, account = yield [
      auth_models.current_account_async(),
      auth_models.Account.email_key(key.id()).get_async()
    ]
    if me is None:
      raise exceptions.NeedsLogin()

    # TODO(iannucci):  Old user popup included # of issues created and
    # # of issues 'reviewed' (which was really just the number of issues which
    # currently have IN review). I've skipped this because it's pretty lame and
    # I don't think anyone was actually using it for anything :).
    #
    # A better popup implementation can be visited later.
    if account.user == me.user:
      raise ndb.Return(account.to_dict())
    else:
      raise ndb.Return(account.to_dict(include=['user', 'nickname']))

  @rest_handler.skip_xsrf
  def get_me_async(self, _key):
    me = yield auth_models.current_account_async()
    raise ndb.Return(me.to_dict())
