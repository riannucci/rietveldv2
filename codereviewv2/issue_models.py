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

import json
import re

from google.appengine.api import taskqueue, users, mail
from google.appengine.ext import ndb
from google.appengine.ext.ndb import metadata

import cas

from framework import (utils, mixins, exceptions, authed_model, account,
                       query_parser)

from . import auth_models, diff

PATCHSET_TYPE = 'application/patchset+json'


@ndb.tasklet
def limit_entities_async(model_name, base_key, limit):
  ret = []
  futures = [
    ndb.Key(pairs=base_key.pairs() + ((model_name, i),)).get_async()
    for i in range(1, limit+1)
  ]
  for f in futures:
    try:
      ret.append((yield f))
    except exceptions.Forbidden:
      pass
  raise ndb.Return(ret)


class LowerEmailProperty(ndb.StringProperty):
  def _validate(self, value):
    value = value.lower()
    if not mail.is_email_valid(value):
      raise TypeError('%r does not appear to be a valid email address' % value)
    return value


class Content(diff.Diffable):
  PATH_RE = re.compile(r'[\x00-\x1F\'"*:<>?|]')

  def __init__(self, content):
    path = content.pop('path')
    mode = content.pop('mode')
    timestamp = content.pop('timestamp')
    data = cas.models.CAS_ID.from_dict(content.pop('data'))
    assert not content
    assert not self.PATH_RE.search(path)
    # TODO(iannucci): Support symlinks and gitlinks?
    assert mode in (100755, 100644)

    # TODO(iannucci): Support variable line endings depending on the
    lineending = '\n' if data.content_type.startswith('text/') else None
    super(Content, self).__init__(path, timestamp, mode, lineending)
    self.cas_id = data

  @utils.cached_property
  def data_async(self):
    return self.cas_id.data_async()

  @utils.cached_property
  def size(self):
    return self.cas_id.size

  def to_dict(self):
    r = super(Content, self).to_dict()
    r.update(
      data=self.cas_id.to_dict()
    )
    return r


class Patch(diff.DiffablePair):
  @classmethod
  def from_dict(cls, id, patch):
    old = Content(patch.pop('old')) if 'old' in patch else None
    new = Content(patch.pop('new')) if 'new' in patch else None
    action = patch.pop('action', None)
    assert not patch
    return cls(id, old, new, action)

  def __init__(self, id, old, new, action):
    super(Patch, self).__init__(old, new, action)
    self.id = id
    self.comments = []
    self.next = None
    self.prev = None
    self.next_with_comment = None
    self.prev_with_comment = None

  def get_data_futures(self):
    return [self.old.data_async, self.new.data_async]

  @utils.cached_property
  def size(self):
    return self.old.size + self.new.size

  def to_dict(self):
    r = super(Patch, self).to_dict()
    r.update(
      id=self.id,
      comments=[c.to_dict() for c in self.comments],
      next=getattr(self.next, 'id', None),
      prev=getattr(self.prev, 'id', None),
      next_with_comment=getattr(self.next_with_comment, 'id', None),
      prev_with_comment=getattr(self.prev_with_comment, 'id', None),
    )
    return r


class PatchList(list):
  @utils.cached_property
  def CAS_REFERENCES(self):
    ret = []
    for patch in self:
      if patch.old:
        ret.append(patch.old.cas_id)
      if patch.new:
        ret.append(patch.new.cas_id)
    return ret


@cas.default_content_types.TYPE_MAP(PATCHSET_TYPE, require_charset='utf-8')
@ndb.tasklet
def patchset_json(data):
  """The json that a client will upload, containing links to other CASEntries.

  {
    "patches": [
      {
        "action": "copy"|"rename",  # optional
        # old or new can be missing if this is an add or a delete.
        "old": {
          "data": {"type": ..., "size": ..., "csum": ...},
          "path": ..., "mode": ..., "timestamp": ...,
        },
        "new": # same as old
      },
    ]
  }
  """
  type_map = cas.default_content_types.TYPE_MAP
  parsed = yield type_map['data', 'application/json'](data)

  assert ['patches'] == parsed.keys()
  assert parsed['patches']
  raise ndb.Return(
    PatchList(Patch.from_dict(i, p) for i, p in enumerate(parsed['patches'])))


def no_extra(data):
  if data:
    raise exceptions.BadData('Got extra data: %r' % (data,))


class Issue(mixins.HideableModelMixin, query_parser.StringQueryMixin):
  # Old Issue models won't have a VERSION field at all
  VERSION = ndb.IntegerProperty(default=2, indexed=False)

  created = ndb.DateTimeProperty(auto_now_add=True)
  modified = ndb.DateTimeProperty(auto_now=True)

  subject = ndb.StringProperty()
  description = ndb.TextProperty()

  owner = auth_models.AccountProperty(auto_current_user_add=True)

  cc = LowerEmailProperty(repeated=True)
  reviewers = LowerEmailProperty(repeated=True)

  closed = ndb.BooleanProperty(default=False)
  private = ndb.BooleanProperty()

  ## Metadata
  last_patchset = ndb.IntegerProperty(default=0)
  last_message = ndb.IntegerProperty(default=0)

  n_comments = ndb.IntegerProperty(default=0)
  n_messages = ndb.IntegerProperty(default=0)
  n_patchsets = ndb.IntegerProperty(default=0)
  # {email: bool}
  approval_status = ndb.JsonProperty(compressed=True)
  notifications = ndb.JsonProperty(compressed=True)


  #### Factory Function
  @classmethod
  @ndb.tasklet
  def create_async(cls, subject='', description='', cc=(),
                   reviewers=(), private=False, **data):
    no_extra(data)
    issue = cls(
      subject=subject,
      description=description,
      cc=cc,
      reviewers=reviewers,
      private=private,
    )
    # Need to allocate id so we can start using the id.
    yield issue.put_async()
    issue.set_notification_async('issue.create', issue.key)
    raise ndb.Return(issue)

  #### Notifications
  @ndb.tasklet
  def flush_to_ds_async(self):
    yield super(Issue, self).flush_to_ds_async(), self.notify_async()

  @ndb.tasklet
  def notify_async(self):
    if self.notifications:
      yield taskqueue.Task(
        # no name, due to requirements of transactional tasks
        url='/restricted/issue_notify',
        payload=json.dumps({'issue': self.key.urlsafe()}),
        headers = {'Content-Type': 'application/json'}
      ).add_async('issue-notify', transactional=True)

  @ndb.tasklet
  def set_notification_async(self, notification, key):
    self.notifications = self.notifications or {}
    lst = set(self.notifications.setdefault(notification, []))
    lst.update([key.urlsafe()])
    self.notifications[notification] = list(lst)
    yield self.mark_async()

  #### Datastore Read Operations
  @utils.cached_property
  def messages_async(self):  # pylint: disable=E0202
    return limit_entities_async('Message', self.key, self.last_message)

  @utils.cached_property
  def patchsets_async(self):  # pylint: disable=E0202
    return limit_entities_async('Patchset', self.key, self.last_patchset)

  @classmethod
  def metadata_key(cls, issue_key):
    assert issue_key.kind() == 'Issue' and issue_key.parent() is None
    return ndb.Key(
      pairs=(
        auth_models.Account.current_user_key() +
        [('IssueMetadata', issue_key.id())]
      ))

  @utils.cached_assignable_property
  def metadata_async(self):
    return self.metadata_key(self.key).get_async()

  #### Entity Manipulation
  @ndb.tasklet
  def update_async(self, **data):
    @ndb.tasklet
    def _apply(key, xform=lambda x: x):
      cur = getattr(self, key)
      new = data.pop(key, None)
      if new is not None:
        new = xform(new)
        if cur != new:
          yield self.mark_async()
          setattr(self, key, new)
          raise ndb.Return(True)
      raise ndb.Return(False)

    reviewers, cc, description = yield [
      _apply('reviewers', lambda vals: map(users.User, vals)),
      _apply('cc', lambda vals: map(users.User, vals)),
      _apply('description'),
      [_apply('subject'), _apply('private'), _apply('closed')]
    ]
    if reviewers or cc or description:
      del self.viewers
    if description:
      del self.collaborators
      del self.editors
    no_extra(data)

  @ndb.tasklet
  def add_patchset_async(self, cas_id_fut, message=None):
    cas_id = yield cas_id_fut
    assert cas_id.children_proven == True
    if cas_id.content_type != PATCHSET_TYPE:
      raise exceptions.BadData('Patchset must have datatype %r'
                               % PATCHSET_TYPE)
    ps = Patchset(id=self.last_patchset+1, message=message, data_ref=cas_id,
                  parent=self.key)
    self.last_patchset += 1
    self.n_patchsets += 1
    yield ps.mark_async()
    (yield self.patchsets_async).append(ps)
    yield self.set_notification_async('patchset.create', ps.key)
    raise ndb.Return(ps)

  @ndb.tasklet
  def del_patchset_async(self, ps):
    self.n_patchsets -= 1
    self.n_comments  -= len(ps.raw_comments)
    yield self.mark_async(), ps.delete_async()

  @ndb.tasklet
  def add_message_async(self, body='', subject=None, drafts=None):
    if subject is None:
      subject = self.subject
    m = Message(
      id=self.last_message+1, body=body, subject=subject,
      recipients=self.viewers
    )
    # TODO(iannucci): Double-check all upper/lowercase email matching.
    if not self.approval_status:
      self.approval_status = {}
    if m.disapproval:
      self.approval_status[m.sender] = False
    elif m.approval:
      self.approval_status[m.sender] = True
    self.last_message += 1
    self.n_messages += 1
    self.n_comments += drafts

    affected_patchsets = yield [d.patchset.get_async() for d in drafts]
    to_wait = [m.mark_async()]
    for ps, draft in zip(affected_patchsets, drafts):
      guts = draft.to_dict()
      guts.pop('owner', None)
      guts.pop('created', None)
      to_wait.append(ps.add_comment_async(guts))
    yield to_wait
    (yield self.messages_async).append(m)
    yield self.set_notification_async('message.create', m.key)
    raise ndb.Return(m)

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
    return set([self.owner.email()] + self.collaborators)

  @utils.clearable_cached_property
  def viewers(self):
    return set(self.editors + self.cc + self.reviewers)

  #### AuthedModel overrides
  @classmethod
  def can_create_key(cls, _key):
    return account.get_current_user() is not None

  def can_read(self):
    ret = super(Issue, self).can_read()
    if ret:
      if self.private:
        cur_user = account.get_current_user()
        ret = cur_user and cur_user.email() in self.viewers
      else:
        ret = True
    return ret

  def can_update(self):
    cur_user = account.get_current_user()
    return cur_user and cur_user.email() in self.editors

  #### Model overrides
  def to_dict(self, include=None, exclude=None):
    exclude = set(exclude or ())
    exclude.update((
      'hidden', 'last_message', 'last_patchset', 'notifications'
    ))
    r = super(Issue, self).to_dict(include=include, exclude=exclude)
    r['id'] = self.key.id()
    return r


class Comment(ndb.Model):
  id = None

  patch = ndb.IntegerProperty(indexed=False)
  lineno = ndb.IntegerProperty(indexed=False)
  # 0 is left, 1 is right
  side = ndb.IntegerProperty(indexed=False, choices=(0, 1))
  body = ndb.TextProperty()

  owner = auth_models.AccountProperty(auto_current_user_add=True, indexed=False)
  created = ndb.DateTimeProperty(auto_now_add=True, indexed=False)

  def populate(self, **data):
    assert 'owner' not in data
    assert 'created' not in data
    super(Comment, self).populate(**data)

  def to_dict(self, *args, **kwargs):
    ret = super(Comment, self).to_dict(*args, **kwargs)
    assert self.id is not None
    ret['id'] = self.id


class Patchset(mixins.HideableModelMixin):
  message = ndb.TextProperty(indexed=False)
  data_ref = cas.models.CAS_IDProperty(PATCHSET_TYPE, 'utf-8')

  created_by = auth_models.AccountProperty(auto_current_user_add=True,
                                        indexed=False)
  created = ndb.DateTimeProperty(indexed=False, auto_now_add=True)

  # This is the only mutable field in Patchset
  raw_comments = ndb.LocalStructuredProperty(Comment, repeated=True,
                                             compressed=True)

  #### Datastore Read Functions
  @utils.clearable_cached_property
  @ndb.tasklet
  def patches_async(self):
    all_patches = yield self.data_ref.data_async()

    comments_by_patch = {}
    for c in self.comments:
      comments_by_patch.setdefault(c.patch, []).append(c)

    prev = None
    prev_with_comment = None
    for i, p in enumerate(all_patches):
      p.prev = prev
      p.prev_with_comment = prev_with_comment
      p.comments = comments_by_patch.get(i, [])
      prev = p
      if p.comments:
        prev_with_comment = p

    next = None
    next_with_comment = None
    for p in reversed(all_patches):
      p.next = next
      p.next_with_comment = next_with_comment
      next = p
      if p.comments:
        next_with_comment = p

    # TODO(iannucci): Apply fancy filename sorting algo here

    raise ndb.Return(all_patches)

  #### Helpers
  @utils.cached_property
  def comments(self):
    for i, c in enumerate(self.raw_comments):
      c.id = i
    return self.raw_comments

  #### RESTModelMixin overrides
  @classmethod
  @ndb.tasklet
  def read_key_async(cls, key, recurse=False):
    # TODO(iannucci): Implement recurse
    # TODO(iannucci): Include patchset CAS entry
    assert not recurse
    ent = yield key.get_async()
    raise ndb.Return(ent.to_dict())

  #### Entity manipulation
  @ndb.tasklet
  def add_comment_async(self, data):
    assert 'created' not in data
    assert 'owner' not in data
    c = Comment()
    c.populate(**data)
    self.comments.append(c)
    self.mark_async()

  #### AuthedModel overrides
  @classmethod
  def can_create_key(cls, key):
    return Issue.can('update', key.parent())

  @classmethod
  def can_update_key(cls, key):
    return Issue.can('update', key.parent())

  def can_read(self):
    return (super(Patchset, self).can_read() and
            self.root_async.get_result().can('read'))

  #### Model overrides
  def to_dict(self, include=None, exclude=None):
    exclude = set(exclude or ())
    exclude.add('hidden')
    exclude.add('raw_comments')
    r = super(Patchset, self).to_dict(include, exclude)
    r['id'] = self.key.id()
    if not include or 'comments' in include:
      r['comments'] = [c.to_dict() for c in self.comments]
    if not include or 'data_ref' in include:
      r['data_ref'] = self.data_ref.to_dict()
    return r


class Message(authed_model.AuthedModel):
  subject = ndb.StringProperty(indexed=False)
  sender = auth_models.AccountProperty(auto_current_user_add=True,
                                       indexed=False)
  recipients = LowerEmailProperty(indexed=False, repeated=True)
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


class DraftComment(Comment):
  patchset = ndb.KeyProperty(kind=Patchset)
  modified = ndb.DateTimeProperty(auto_now=True)

  def populate(self, **data):
    assert 'modified' not in data
    data['patchset'] = ndb.Key(urlsafe=data['patchset'])
    super(DraftComment, self).populate(**data)


class IssueMetadata(authed_model.AuthedModel):
  raw_drafts = ndb.LocalStructuredProperty(DraftComment, repeated=True)

  had_updates = ndb.BooleanProperty()
  acknowledged_messages = ndb.IntegerProperty(repeated=True)
  ent_version = ndb.IntegerProperty()

  n_drafts = ndb.ComputedProperty(lambda self: len(self.raw_drafts))
  starred = ndb.BooleanProperty(default=False)

  #### API manipulation
  @classmethod
  @ndb.transactional_tasklet
  def update_async(cls, my_key, drafts, **data):
    no_extra(data)
    ent = yield my_key.get_async()
    if ent is None:
      ent = cls(key=my_key)

    for draft in drafts:
      d_id = draft.pop('id', None)
      if d_id is not None:
        d_ent = ent.drafts[d_id]
      else:
        d_ent = DraftComment()
        d_ent.id = len(ent.drafts)
        ent.drafts.append(d_ent)
      d_ent.populate(**draft)

    yield ent.put_async()

  #### Properties
  @utils.cached_property
  def drafts(self):
    for i, d in enumerate(self.raw_drafts):
      d.id = i
    return self.drafts

  @property
  def issue_key(self):
    return ndb.Key('Issue', self.key.id())

  @utils.cached_property
  @ndb.tasklet
  def has_updates_async(self):
    key = metadata.EntityGroup.key_for_entity_group(self.issue_key)
    eg = yield key.get_async()
    if eg.version > self.ent_version:
      self.ent_version = eg.version
      issue, account = yield self.issue_key.get_async(), self.root_async
      messages = yield issue.messages_async
      self.had_updates = (
        messages[-1].sender != account.email and
        messages[-1].key.id() not in self.acknowledged_messages
      )
      yield self.put_async()
    raise ndb.Return(self.had_updates)

  #### AuthedModel overrides
  @classmethod
  def can_create_key(cls, key):
    cur_user = account.get_current_user()
    return cur_user and cur_user.email() == key.parent().id()

  @classmethod
  def can_read_key(cls, key):
    return cls.can('create', key)

  @classmethod
  def can_write_key(cls, key):
    return cls.can('create', key)
