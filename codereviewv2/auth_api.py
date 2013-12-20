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
  def read_one_async(self, key):
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
  def read_me_async(self, _key):
    me = yield auth_models.current_account_async()
    raise ndb.Return(me.to_dict())
