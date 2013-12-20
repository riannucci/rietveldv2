import logging

from google.appengine.api import oauth
from google.appengine.api import users
from google.appengine.ext import ndb

from django.conf import settings

from framework import xsrf
from framework.authed_model import AuthedModel

EMAIL_SCOPE = 'https://www.googleapis.com/auth/userinfo.email'


def _get_current_rietveld_oauth_user():
  """Gets the current OAuth 2.0 user associated with a request.

  This user must be intending to reach this application, so we check the token
  info to verify this is the case.

  Returns:
    A users.User object that was retrieved from the App Engine OAuth library if
        the token is valid, otherwise None.
  """
  # TODO(dhermes): Address local environ here as well.
  try:
    current_client_id = oauth.get_client_id(EMAIL_SCOPE)
  except oauth.Error:
    return

  accepted_client_id, _, additional_client_ids = _SecretKey.get_config()
  if (accepted_client_id != current_client_id and
      current_client_id not in additional_client_ids):
    logging.debug('Client ID %r not intended for this application.',
                  current_client_id)
    return

  try:
    return oauth.get_current_user(EMAIL_SCOPE)
  except oauth.Error:
    logging.warning('A Client ID was retrieved with no corresponsing user.')


def get_current_user():
  """Gets the current user associated with a request.

  First tries to verify a user with the Users API (cookie-based auth), and then
  falls back to checking for an OAuth 2.0 user with a token minted for use with
  this application.

  Returns:
    A users.User object that was retrieved from the App Engine Users or OAuth
        library if such a user can be determined, otherwise None.
  """
  return users.get_current_user() or _get_current_rietveld_oauth_user()


class _SecretKey(ndb.Model):
  """Model for representing project secret keys."""
  client_id = ndb.StringProperty(required=True, indexed=False)
  client_secret = ndb.StringProperty(required=True, indexed=False)
  additional_client_ids = ndb.StringProperty(repeated=True, indexed=False)

  GLOBAL_KEY = '_global_config'

  @classmethod
  def _get_kind(cls):
    return 'SecretKey'

  @classmethod
  def set_config(cls, client_id, client_secret, additional_client_ids=None):
    """Sets global config object using a Client ID and Secret.

    Args:
      client_id: String containing Google APIs Client ID.
      client_secret: String containing Google APIs Client Secret.
      additional_client_ids: List of strings for Google APIs Client IDs which
        are allowed against this application but not used to mint tokens on
        the server. For example, service accounts which interact with this
        application. Defaults to None.

    Returns:
      The inserted SecretKey object.
    """
    additional_client_ids = additional_client_ids or []
    config = cls(id=cls.GLOBAL_KEY,
                 client_id=client_id, client_secret=client_secret,
                 additional_client_ids=additional_client_ids)
    config.put()
    return config

  @classmethod
  def get_config(cls):
    """Gets tuple of Client ID and Secret from global config object.

    Returns:
      3-tuple containing the Client ID and Secret from the global config
          SecretKey object as well as a list of other allowed client IDs, if the
          config is in the datastore, else the tuple (None, None, []).
    """
    config = cls.get_by_id(cls.GLOBAL_KEY)
    if config is None:
      return None, None, []
    else:
      return (config.client_id, config.client_secret,
              config.additional_client_ids)


class UserProperty(ndb.UserProperty):
  """An extension of the ndb.UserProperty which also accepts OAuth users.

  The default ndb.UserProperty only considers cookie-based Auth users.

  Using:

    class FooNDB(ndb.Model):
      bar = UserProperty(auto_current_user=True)

  with f = FooNDB() will have f.bar equal to None until the entity has been
  stored.

  To make the behavior here take effect immediately, something similar to

  ('https://github.com/GoogleCloudPlatform/endpoints-proto-datastore/blob'
   '/511c8ca87d7548b90e9966495e9ebffd5eecab6e/endpoints_proto_datastore/'
   'ndb/properties.py#L215')

  could be implementated.
  """

  def _prepare_for_put(self, entity):
    """Custom hook to prepare the entity for a datastore put.

    If auto_current_user or auto_current_user_add is used, will
    set the current user using the get_current_user() method from this module.

    Args:
      entity: A Protobuf entity to store data.
    """
    if (self._auto_current_user or
        (self._auto_current_user_add and not self._has_value(entity))):
      value = get_current_user()
      if value is not None:
        self._store_value(entity, value)

  def _user_value_from_str_async(self, val):
    return Account.get_by_email_async(val)


class Account(AuthedModel):
  CONTEXT_CHOICES = (3, 10, 25, 50, 75, 100)

  user = UserProperty(auto_current_user_add=True, required=True)

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
  lower_email = ndb.ComputedProperty(lambda self: self._get_email.lower())  # pylint: disable=W0212
  lower_nickname = ndb.ComputedProperty(lambda self: self.nickname.lower())

  created = ndb.DateTimeProperty(auto_now_add=True)
  modified = ndb.DateTimeProperty(auto_now=True)

  @classmethod
  def email_key(cls, email):
    return ndb.Key('Account', '<%s>' % email)

  @classmethod
  def current_user_key(cls):
    user = get_current_user()
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
    if not hasattr(ctx, 'codereview_account'):
      user = get_current_user()

      account = None
      if user is not None:
        email = user.email()
        key = cls.email_key(email)
        account = yield key.get_async()
        if account is None:
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
          uniquifier = hash(user.user_id()) % 1337

          account = cls(
            key=key,
            user=user,
            nickname='%s (%s)' % (email[email.index('@')+1:], uniquifier),
          )
          account.put_async()  # why wait?
      ctx.codereview_account = account
    else:
      account = ctx.codereview_account
    raise ndb.Return(account)

  def to_dict(self, *args, **kwargs):
    kwargs.setdefault('exclude', set()).add('blocked')
    ret = super(Account, self).to_dict(*args, **kwargs)
    if self.user == get_current_user():
      ret['xsrf'] = xsrf.make_header_token(self.user)
    return ret


def current_account_async():
  return Account.current_async()


def current_account():
  return current_account_async().get_result()
