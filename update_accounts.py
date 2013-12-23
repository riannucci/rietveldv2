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

# Removes duplicate nicknames (issue99).
#
# To run this script:
#  - Make sure App Engine library (incl. yaml) is in PYTHONPATH.
#  - Make sure that the remote API is included in app.yaml.
#  - Run "tools/appengine_console.py APP_ID".
#  - Import this module.
#  - update_accounts.run() updates accounts.
#  - Use the other two functions to fetch accounts or find duplicates
#    without any changes to the datastore.


from google.appengine.ext import db

from codereview import models


def fetch_accounts():
    query = models.Account.all()
    accounts = {}
    results = query.fetch(100)
    while results:
        last = None
        for account in results:
            if account.lower_nickname in accounts:
                accounts[account.lower_nickname].append(account)
            else:
                accounts[account.lower_nickname] = [account]
            last = account
        if last is None:
            break
        results = models.Account.all().filter('__key__ >',
                                              last.key()).fetch(100)
    return accounts


def find_duplicates(accounts):
    tbd = []
    while accounts:
        _, entries = accounts.popitem()
        if len(entries) > 1:
            # update accounts, except the fist: it's the lucky one
            for num, account in enumerate(entries[1:]):
                account.nickname = '%s%d' % (account.nickname, num+1)
                account.lower_nickname = account.nickname.lower()
                account.fresh = True  # display "change nickname..."
                tbd.append(account)
    return tbd


def run():
    accounts = fetch_accounts()
    print '%d accounts fetched' % len(accounts)

    tbd = find_duplicates(accounts)
    print 'Updating %d accounts' % len(tbd)

    db.put(tbd)

    print 'Updated accounts:'
    for account in tbd:
        print ' %s' % account.email
