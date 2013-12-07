import collections
import json
import re

from google.appengine.api import taskqueue, users
from google.appengine.ext import ndb

from django.conf import settings

from .. import cas
from ..framework import utils, mixins, query_hooks, exceptions
from ..framework.authed_model import AuthedModel

from . import auth_utils

query_hooks.monkeypatch()

TYPE_MAP = cas.CASTypeRegistry(cas.DEFAULT_TYPE_MAP)

PATCHSET_TYPE = 'application/patchset+json'


@TYPE_MAP(PATCHSET_TYPE)
def patchset_json(data):
  """The json that a client will upload, containing links to other CASEntries.

  {
    "patches": [
      {
        "old": {
          "type": ..., "size": ..., "csum": ...,
          "path": ..., "executable": ..., "timestamp": ...,
        },
        "new": # same as old
        "diff": {
          # type/size/csum must be null if old+new are not text
          # type must be 'text/plain+universal_diff'
          "type": ..., "size": ..., "csum": ...,
          "prefix": ...,
          "suffix": ...,
        }
      },
    ]
  }
  """
  parsed = TYPE_MAP['application/json'](data)
  assert ['patches'] == parsed.keys()
  assert parsed['patches']

  class DictWithRefs(dict):
    def __init__(self, *args, **kwargs):
      super(DictWithRefs, self).__init__(*args, **kwargs)
      self.CAS_REFERENCES = []

  parsed = DictWithRefs(parsed)
  for patch in parsed['patches']:
    assert ('old', 'new', 'diff') == patch.keys()
    is_text = True
    for content in (patch['old'], patch['new']):
      keys = ['type', 'size', 'csum', 'path', 'executable', 'timestamp']
      assert keys == content.keys()
      parsed.CAS_REFERENCES.append(cas.models.CAS_ID.fromdict(content))
      if not content['type'].startswith('text/'):
        is_text = False
      # Prevent paths from being problematic on windows.
      assert not re.search(r'[\x00-\x1F"*:<>?\/|]', content['path'])
      assert isinstance(content['executable'], bool)
    diff = patch['diff']
    if not is_text:
      assert diff['type'] is diff['size'] is diff['csum'] is None
    else:
      parsed.CAS_REFERENCES.append(cas.models.CAS_ID.fromdict(diff))
  return parsed


class Account(AuthedModel):
  CONTEXT_CHOICES = (3, 10, 25, 50, 75, 100)

  user = auth_utils.NdbAnyAuthUserProperty(auto_current_user_add=True,
                                           required=True)
  xsrf_secret = ndb.BlobProperty()

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
  stars = ndb.IntegerProperty(repeated=True)

  # notification preferences
  notify_by_email = ndb.BooleanProperty(default=True)
  notify_by_chat = ndb.BooleanProperty(default=False)

  # spammer options
  blocked = ndb.BooleanProperty(default=False)

  ### Automatic
  lower_email = ndb.ComputedProperty(lambda self: self.email.lower())
  lower_nickname = ndb.ComputedProperty(lambda self: self.nickname.lower())

  created = ndb.DateTimeProperty(auto_now_add=True)
  modified = ndb.DateTimeProperty(auto_now=True)

  @utils.cached_property
  def email(self):
    return self.key.id().strip('<>')

  @classmethod
  @ndb.tasklet
  def get_for_user_async(cls, user):
    email = user.email()
    assert email

    account = yield cls.get_by_id_async('<%s>' % email)
    if account is None:
      # NOTE: Technically this is racy, since a user could theoretically be
      # in this code from multiple sessions. But the way to fix it would be
      # to use get_or_insert, which uses a transaction. Since we need that
      # transaction for the actual Issue, we'll live on the wild side.

      # Originally we tried to uniqify the nickname based on a db query.
      # The new method will deterministically generate really ugly (but
      # very likely non-conflicting) default nicknames. We assert that this is
      # fine since they'll get an ugly red banner until they pick a real one
      # anyway.

      # we have to treat user_id() as a str according to the docs, so turn it
      # into a long by pretending it's base 256.
      user_id_long = sum(ord(b) << i*8 for i, b in enumerate(user.user_id()))
      ugly_uniquifier = user_id_long % 1337

      account = yield account.put_async(
        user=user,
        nickname='%s (%s)' % (email[email.index('@')+1:], ugly_uniquifier)
      )
    raise ndb.Return(account)

  @classmethod
  @ndb.tasklet
  def current_async(cls):
    ctx = ndb.get_context()
    if not hasattr(ctx, 'codereview_account'):
      user = auth_utils.get_current_user()
      if user is not None:
        account = yield cls.get_for_user_async(user)
        ctx.codereview_account = account
    else:
      account = ctx.codereview_account
    raise ndb.Return(account)


class Issue(AuthedModel, mixins.HideableModel, mixins.EntityTreeMixin):
  # Old Issue models won't have a VERSION field at all
  VERSION = ndb.IntegerProperty(default=2, indexed=False)

  created = ndb.DateTimeProperty(auto_now_add=True)
  modified = ndb.DateTimeProperty(auto_now=True)

  subject = ndb.StringProperty()
  description = ndb.TextProperty()

  owner = auth_utils.NdbAnyAuthUserProperty(auto_current_user_add=True)

  cc = auth_utils.NdbAnyAuthUserProperty(repeated=True)
  reviewers = auth_utils.NdbAnyAuthUserProperty(repeated=True)

  closed = ndb.BooleanProperty(default=False)
  private = ndb.BooleanProperty()

  #### Factory Function
  @classmethod
  @ndb.tasklet
  def create_async(cls, subject, description, cc, reviewers, private):
    """Primary creation method."""
    # Need to actually put this so we can start using the id.
    issue = yield cls(
      subject=subject,
      description=description,
      cc=cc,
      reviewers=reviewers,
      private=private,
    ).put_async()
    issue.notifications['issue.create'].add(issue.key)

    mdata = IssueMetaData(parent=issue.key)
    yield mdata.mark_async()

    issue.metadata_async = utils.completed_future(mdata)
    issue.patchsets_async = utils.completed_future([])
    issue.messages_async = utils.completed_future([])

    raise ndb.Return(issue)

  #### Notifications
  @ndb.tasklet
  def flush_to_ds_async(self):
    yield super(Issue, self).flush_to_ds_async(), self.notify_async()

  @utils.cached_property
  def notifications(self):
    return collections.defaultdict(set)

  @ndb.tasklet
  def notify_async(self):
    task = taskqueue.Task(
      # no name, due to requirements of transactional tasks
      url='/restricted/issue_notify',
      payload=json.dumps({
        k: map(ndb.Key.urlsafe, v) for k, v in self.notifications.iteritems()
      }),
      headers = {'Content-Type': 'application/json'}
    )
    yield task.add_async('issue_notify', transactional=True)
    self.notifications.clear()

  #### Datastore Read Operations
  @utils.cached_assignable_property
  def messages_async(self):
    return Message.query(ancestor=self.key).fetch_async()

  @utils.cached_assignable_property
  def patchsets_async(self):
    return Patchset.query(ancestor=self.key).fetch_async()

  @utils.cached_assignable_property
  def metadata_async(self):
    return IssueMetaData.get_for_async(self)

  #### Entity Manipulation
  @ndb.tasklet
  def modify_from_dict_async(self, data):
    @ndb.tasklet
    def _apply(key, xform=lambda x: x):
      cur = getattr(self, key)
      new = data.get(key, None)
      if new is not None:
        new = xform(new)
        if cur != new:
          yield self.mark_async()
          setattr(self, key, new)
          raise ndb.Return(True)
      raise ndb.Return(False)

    yield _apply('subject'), _apply('private'), _apply('closed')
    if (yield _apply('reviewers', lambda vals: map(users.User, vals))):
      del self.viewers
    if (yield _apply('cc', lambda vals: map(users.User, vals))):
      del self.viewers
    if (yield _apply('description')):
      del self.collaborators
      del self.editors
      del self.viewers

  @ndb.tasklet
  def add_patchset_async(self, cas_id, message=None):
    if cas_id.data_type != PATCHSET_TYPE:
      raise exceptions.BadData('Patchset must have datatype %r' % PATCHSET_TYPE)
    ps = Patchset(message=message, data_ref=cas_id.as_key(),
                  parent=self.key)
    ps.mark()
    mdata, patchsets = yield self.metadata_async, self.patchsets_async
    mdata.add_patchset(ps)
    patchsets.append(ps)
    self.notifications['patchset.create'].add(ps.key)

  @ndb.tasklet
  def add_message_async(self, *args, **kwargs):
    m = Message(*args, **kwargs)
    m.mark()
    mdata, messages = yield self.metadata_async, self.messages_async
    mdata.add_message(m)
    messages.append(m)
    self.notifications['message.create'].add(m.key)

  #### In-memory data accessors
  @utils.clearable_cached_property
  def collaborators(self):
    prefix = 'COLLABORATOR='
    ret = []
    for line in self.description.splitlines():
      if line.startswith(prefix):
        ret.append(line[len(prefix):].strip())
    return ret

  @utils.clearable_cached_property
  def editors(self):
    return set([self.owner] + self.collaborators)

  @utils.clearable_cached_property
  def viewers(self):
    return set(self.editors + self.cc + self.reviewers)

  #### AuthedModel overrides
  @classmethod
  def can_create_key(cls, _key):
    return Account.current_async().get_result() is not None

  def can_read(self):
    if self.private:
      account = Account.current_async().get_result()
      return account and account.email in self.viewers
    else:
      return True

  def can_update(self):
    account = Account.current_async().get_result()
    return account and account.email in self.editors


class PatchMetaData(ndb.Model):
  pass

class PatchsetMetaData(ndb.Model):
  patches = ndb.LocalStructuredProperty(PatchMetaData, repeated=True)


class IssueMetaData(mixins.EntityTreeMixin):
  patchsets = ndb.LocalStructuredProperty(PatchsetMetaData,
                                          compressed=True,
                                          repeated=True)

  n_comments = ndb.IntegerProperty(default=0)
  n_messages_sent = ndb.IntegerProperty(default=0)

  @classmethod
  def get_for_async(cls, issue):
    return ndb.Key(pairs=issue.key.pairs() + (IssueMetaData, 1)).get_async()


class Patchset(AuthedModel, mixins.HideableModel, mixins.EntityTreeMixin):
  message = ndb.TextProperty(indexed=False)
  data_ref = ndb.KeyProperty(kind=cas.models.CASEntry)

  created_by = auth_utils.NdbAnyAuthUserProperty(auto_current_user_add=True,
                                                 indexed=False)
  created = ndb.DateTimeProperty(indexed=False, auto_now_add=True)

  #### Datastore Read Functions
  @utils.cached_property
  def raw_data(self):
    entry = self.data_ref.get()
    assert entry.data_type == self.RAW_DATA_TYPE
    return json.loads(entry.data.get_result())

  @utils.cached_property  # Keeps ref to all_patches alive
  def patches(self):
    # Make sure to match ident to the order in the raw_data. This will ensure
    # that patches have stable ID's, no matter what sorting algorithm we use
    # for presentation purposes.
    all_patches = map(Patch, enumerate(self.raw_data['patches']))

    # TODO(iannucci): Apply fancy filename sorting algo here
    for i, patch in enumerate(all_patches):
      patch.all_patches = all_patches
      patch.all_patches_idx = i
    return all_patches

  #### Entity Manipulation
  @ndb.tasklet
  def delete_async(self):
    # TODO(iannucci): If this patchset had comments, update Issue metadata.
    issue, _ = yield self.root_async, super(Patchset, self).delete_async()
    mdata = yield issue.metadata_async
    mdata.delete_patchset(self)

  #### AuthedModel overrides
  @classmethod
  def can_create_key(cls, key):
    return Issue.can('update', ndb.Key(flat=key.pairs()[0]))

  def can_read(self):
    return self.root_async.get_result().can('read')


class Content(object):
  def __init__(self, content):
    pass


class Patch(object):
  def __init__(self, (ident, patch)):
    self.ident = ident
    self.patch = patch
    self.old = Content(self.patch['old'])
    self.new = Content(self.patch['new'])
    self.diff = self.patch['diff']

    self.all_patches = None
    self.all_patches_idx = None

  def _offset(self, inc, criterion=lambda p: True):
    if self.all_patches is not None:
      idx = self.all_patches_idx + inc
      while 0 <= idx < len(self.all_patches):
        el = self.all_patches[idx]
        if criterion(el):
          return el
        idx += inc

  @utils.cached_property
  def next(self):
    return self._offset(1)

  @utils.cached_property
  def prev(self):
    return self._offset(-1)

  @utils.cached_property
  def next_with_comment(self):
    return self._offset(1, lambda p: p.metadata.n_comments)

  @utils.cached_property
  def prev_with_comment(self):
    return self._offset(-1, lambda p: p.metadata.n_comments)


class Message(AuthedModel, mixins.EntityTreeMixin):
  subject = ndb.StringProperty(indexed=False)
  sender = auth_utils.NdbAnyAuthUserProperty(auto_current_user_add=True,
                                             indexed=False)
  recipients = auth_utils.NdbAnyAuthUserProperty(indexed=False, repeated=True)
  body = ndb.TextProperty()

  # Automatically set on put()
  approval = ndb.BooleanProperty(indexed=False, default=False)
  disapproval = ndb.BooleanProperty(indexed=False, default=False)
  issue_was_closed = ndb.BooleanProperty()
  created = ndb.DateTimeProperty(indexed=False, auto_now_add=True)

  #### Hook overrides
  def _pre_put_hook(self):
    super(Message, self)._pre_put_hook()
    self.issue_was_closed = self.root_async.get_result().closed
    for line in (l.strip() for l in self.body.splitlines()):
      if line.startswith('>'):
        continue
      if 'not lgtm' in line:
        self.approval = False
        self.disapproval = True
        break
      if 'lgtm' in line:
        self.approval = True

  #### AuthedModel overrides
  @classmethod
  def can_create_key(cls, key):
    return Issue.can('read', key.parent())

  def can_read(self):
    return self.root_async.get_result().can('read')


class Comment(AuthedModel, mixins.EntityTreeMixin):
  owner = auth_utils.NdbAnyAuthUserProperty(auto_current_user_add=True,
                                            indexed=False)
  lineno = ndb.IntegerProperty(indexed=False)
  body = ndb.TextProperty()

  # 0 is left, 1 is right
  side = ndb.IntegerProperty(indexed=False)

  created = ndb.DateTimeProperty(auto_now_add=True, indexed=False)

  @classmethod
  def can_create_key(cls, key):
    return Issue.can('read', ndb.Key(flat=key.pairs()[0]))

  def can_read(self):
    return self.root_async.get_result().can('read')


class DraftComment(Comment):
  # Key is:
  # Account > Issue > Patchset > Patch > DraftComment
  updated = ndb.DateTimeProperty(auto_now=True, indexed=False)

  def can_read(self):
    return self.owner == auth_utils.get_current_user()

  def can_write(self):
    return self.can('read')

  def can_delete(self):
    return self.can('read')
