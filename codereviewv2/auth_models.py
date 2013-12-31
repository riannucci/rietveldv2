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

from google.appengine.ext import ndb

from django.conf import settings

from framework import xsrf, account, exceptions


class AccountProperty(account.UserProperty):
  ### Used to enable framework/prop_from_str monkeypatch
  def _user_value_from_str_async(self, val):
    return Account.get_by_email_async(val)


class Account(ndb.Model):
  # TODO(iannucci): Move most of the account stuff to framework
  CONTEXT_CHOICES = (3, 10, 25, 50, 75, 100)

  user = AccountProperty(auto_current_user_add=True, required=True)

  ### Preferences
  nickname = ndb.StringProperty()
  #: indicates if this account has picked a nickname yet
  fresh = ndb.BooleanProperty(default=True)

  # TODO: use LocalStructuredProperties to group preferences.
  # codereview preferences
  default_context = ndb.IntegerProperty(default=settings.DEFAULT_CONTEXT,
                                        choices=CONTEXT_CHOICES)
  default_column_width = ndb.IntegerProperty(
      default=settings.DEFAULT_COLUMN_WIDTH)

  # notification preferences
  notify_by_email = ndb.BooleanProperty(default=True)
  notify_by_chat = ndb.BooleanProperty(default=False)

  # spammer options
  blocked = ndb.BooleanProperty(default=False)

  ### Automatic
  def _get_email(self):
    return self.key.id().strip('<>')
  email = ndb.ComputedProperty(_get_email)
  lower_email = ndb.ComputedProperty(lambda self: self._get_email().lower())  # pylint: disable=W0212
  lower_nickname = ndb.ComputedProperty(lambda self: self.nickname.lower())

  created = ndb.DateTimeProperty(auto_now_add=True)
  modified = ndb.DateTimeProperty(auto_now=True)

  @classmethod
  def email_key(cls, email):
    return ndb.Key('Account', '<%s>' % email)

  @classmethod
  def current_user_key(cls):
    user = account.get_current_user()
    if user is not None:
      return cls.email_key(user.email())

  @classmethod
  def get_for_nick_or_email_async(cls, nick_or_email):
    if '@' in nick_or_email:
      return cls.email_key(nick_or_email).get_async()
    else:
      return cls.query(cls.lower_nickname == nick_or_email.lower()).get_async()

  @classmethod
  @ndb.tasklet
  def current_async(cls):
    ctx = ndb.get_context()
    if not hasattr(ctx, 'account'):
      user = account.get_current_user()

      acnt = None
      if user is not None:
        email = user.email()
        key = cls.email_key(email)
        acnt = yield key.get_async()
        if acnt is None:
          # NOTE: Technically this is racy, since a user could theoretically be
          # in this code from multiple sessions. But the way to fix it would be
          # to use get_or_insert, which uses a transaction. Since we need that
          # transaction for the actual Issue, we'll live on the wild side.

          # Originally we tried to uniqify the nickname based on a db query.
          # The new method will deterministically generate really ugly (but
          # very likely non-conflicting) default nicknames. We assert that this
          # is fine since they'll get an ugly red banner until they pick a real
          # one anyway.

          # we have to treat user_id() as a str according to the docs, so turn
          # it into a long by hashing it.
          hsh = hashlib.md5(user.user_id()).digest()[:8]
          hsh = sum(ord(x) << i for i, x in enumerate(reversed(hsh)))
          uniquifier = hsh % 1337

          acnt = cls(
            key=key,
            user=user,
            nickname='%s (%s)' % (email[:email.index('@')], uniquifier),
          )
          acnt.put_async()  # why wait?
      ctx.account = acnt
    else:
      acnt = ctx.account
    raise ndb.Return(acnt)

  def to_dict(self, *args, **kwargs):
    kwargs.setdefault('exclude', set()).add('blocked')
    ret = super(Account, self).to_dict(*args, **kwargs)
    if self.user == account.get_current_user():
      ret['xsrf'] = xsrf.make_header_token()
    return ret


@ndb.tasklet
def current_account_async(required=True):
  ret = yield Account.current_async()
  if required and not ret:
    raise exceptions.NeedsLogin()
  raise ndb.Return(ret)
