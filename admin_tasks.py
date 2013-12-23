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

"""Collection of mapreduce jobs."""

import logging
from mapreduce import operation as op
from codereview.models import Account, Issue


def delete_unused_accounts(account):
  """Delete accounts for uses that don't participate in any reviews."""
  email = account.user.email()
  if Issue.all().filter('owner_email =', email).get():
    return
  if Issue.all().filter('cc =', email).get():
    return
  if Issue.all().filter('reviewers =', email).get():
    return
  logging.warn('Deleting %s' % email)
  yield op.db.Delete(account)


def update_account_schema(account):
  """Update schema for all Accounts by saving them back to the datastore."""

  # Make sure we don't alter the modified time of any accounts. Because of how
  # mapreduce is designed, we just set this to False on every function
  # invocation (since there's no convenient once-per-instance place to do it).
  Account.modified.auto_now = False

  yield op.db.Put(account)
