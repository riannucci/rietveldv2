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

import collections
import json
import re

from google.appengine.api import taskqueue, mail
from google.appengine.ext import ndb
from google.appengine.ext.ndb import metadata

import cas

from framework import (utils, mixins, exceptions, authed_model, account,
                       query_parser)

from framework.monkeypatch import fix_ndb_hook_context  # pylint: disable=W0611

from . import auth_models, diff

PATCHSET_TYPE = 'application/patchset+json'


class IDIndexedCollection(collections.MutableMapping):
  def __init__(self, name, items=(), parent_key=None, append_callback=None):
    self._name = name
    self._items = list(items)

    self.parent_key = parent_key
    self.append_callback = append_callback

  def _key_for_id(self, id):
    assert self.parent_key is not None
    return ndb.Key(pairs=self.parent_key.pairs() + ((self._name, id),))

  def __getitem__(self, id):
    if not isinstance(id, int) or id < 1 or id > len(self):
      raise exceptions.NotFound(self._key_for_id(id))
    item = self._items[id - 1]
    if item is None:
      raise exceptions.NotFound(self._key_for_id(id))
    return item

  def __delitem__(self, id):
    # Will throw if id is bad or item is gone already
    self.__getitem__(id)
    del self._items[id - 1]

  def __setitem__(self, id, value):
    next_id = len(self._items) + 1
    if id != next_id:
      raise exceptions.FrameworkException('Setting wrong id %s' % id)
    if self.append_callback:
      self.append_callback(value)
    self._items.append(value)

  def append(self, value):
    id = len(self._items) + 1
    self[id] = value
    return id

  def __iter__(self):
    return (i + 1 for i, item in enumerate(self._items) if item is not None)

  def __len__(self):
    return sum(1 for item in self._items if item is not None)


@ndb.tasklet
def limit_entities_async(model_name, base_key, limit):
  ret = IDIndexedCollection(model_name, parent_key=base_key)
  futures = [
    ndb.Key(pairs=base_key.pairs() + ((model_name, i),)).get_async()
    for i in range(1, limit+1)
  ]
  for f in futures:
    try:
      ret.append((yield f))
    except exceptions.NotFound:
      ret.append(None)
  raise ndb.Return(ret)


class LowerEmailProperty(ndb.StringProperty):
  def _validate(self, value):
    value = value.lower()
    if not mail.is_email_valid(value):
      raise TypeError('%r does not appear to be a valid email address' % value)
    return value


class EnumProperty(ndb.IntegerProperty):
  # pylint: disable=E1002
  def __init__(self, *args, **kwargs):
    assert 'choices' in kwargs
    assert isinstance(kwargs['choices'], (list, tuple))
    self._ordered_choices = tuple(kwargs['choices'])
    super(EnumProperty, self).__init__(*args, **kwargs)

  def _to_base_type(self, value):
    return self._ordered_choices.index(value)

  def _from_base_type(self, value):
    return self._ordered_choices[value]


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
    assert mode in (0100755, 0100644)

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

  @utils.cached_property
  @ndb.tasklet
  def git_csum_async(self):
    entry = yield self.cas_id.entry_async()
    raise ndb.Return(entry.git_hash.encode('hex'))

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
    return [self.old.data_async, self.new.data_async,
            self.old.git_csum_async, self.new.git_csum_async]

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


class PatchCollection(IDIndexedCollection):
  @utils.cached_property
  def CAS_REFERENCES(self):
    ret = []
    for patch in self.itervalues():
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

  def forbidden(_value):
    raise exceptions.Forbidden('add/del patches in a patchset')

  raise ndb.Return(
    PatchCollection(
      '$Patches',
      (Patch.from_dict(i+1, p) for i, p in enumerate(parsed['patches'])),
      forbidden,
    ))


def no_extra(data):
  if data:
    raise exceptions.BadData('Got extra data: %r' % (data,))


class Issue(authed_model.AuthedModel, mixins.HideableModelMixin,
            query_parser.StringQueryMixin, mixins.IDModelMixin):
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

  @staticmethod
  @ndb.tasklet
  def key_metadata_async(issue_key):
    assert issue_key.kind() == 'Issue' and issue_key.parent() is None

    cur_user_key = auth_models.Account.current_user_key()
    if cur_user_key:
      key = ndb.Key(
        pairs=(cur_user_key.pairs() + (('IssueMetadata', issue_key.id()),)))
      ent = yield key.get_async()
      if ent is None:
        ent = IssueMetadata(key=key)
        yield ent.put_async()
    else:
      raise exceptions.NeedsLogin()

    raise ndb.Return(ent)

  @utils.cached_property
  def metadata_async(self):
    return self.key_metadata_async(self.key)

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

    reviewers, cc, description, _ = yield [
      _apply('reviewers'),
      _apply('cc'),
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
    self.n_comments += len(drafts)

    affected_patchsets = yield [d.patchset.get_async() for d in drafts]
    to_wait = [m.mark_async()]
    for ps, draft in zip(affected_patchsets, drafts.values()):
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
    ret = set()
    for line in self.description.splitlines():
      if line.startswith(prefix):
        ret.add(line[len(prefix):].strip())
    return ret

  @utils.clearable_cached_property
  def editors(self):
    return self.collaborators | set((self.owner.email(),))

  @utils.clearable_cached_property
  def viewers(self):
    return self.editors | set(self.cc) | set(self.reviewers)

  #### AuthedModel overrides
  @classmethod
  def can_create_key(cls, _key):
    return account.get_current_user() is not None

  def can_read(self):
    ret = True
    if self.private:
      cur_user = account.get_current_user()
      ret = cur_user and cur_user.email() in self.viewers
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
    return super(Issue, self).to_dict(include=include, exclude=exclude)


def validate_not_empty(_prop, val):
  if not val:
    raise exceptions.BadData('Empty comments not allowed.')
  return val

class Comment(ndb.Model):
  id = None

  patch = ndb.IntegerProperty(indexed=False)
  lineno = ndb.IntegerProperty(indexed=False)
  side = EnumProperty(choices=['old', 'new'])

  body = ndb.TextProperty(validator=validate_not_empty)

  owner = auth_models.AccountProperty(auto_current_user_add=True, indexed=False)
  created = ndb.DateTimeProperty(auto_now_add=True, indexed=False)

  context_line = ndb.TextProperty()

  def to_dict(self, include=None, exclude=None):
    ret = super(Comment, self).to_dict(include=include, exclude=exclude)
    assert self.id is not None
    ret['id'] = self.id
    return ret


class Patchset(authed_model.AuthedModel, mixins.HideableModelMixin,
               mixins.IDModelMixin):
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
    all_patches.parent_key = self.key

    comments_by_patch = {}
    for c in self.comments.itervalues():
      comments_by_patch.setdefault(c.patch, []).append(c)

    prev = None
    prev_with_comment = None
    for p in all_patches.itervalues():
      p.prev = prev
      p.prev_with_comment = prev_with_comment
      p.comments = comments_by_patch.get(p.id, [])
      prev = p
      if p.comments:
        prev_with_comment = p

    next = None
    next_with_comment = None
    for p in reversed(all_patches.values()):
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
      c.id = i + 1
    return IDIndexedCollection('$Comment', self.raw_comments, self.key,
                               self.raw_comments.append)

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
  def add_comment_async(self, data):
    assert 'created' not in data
    assert 'owner' not in data
    c = Comment(**data)
    c.id = self.comments.append(c)
    return self.mark_async()

  #### AuthedModel overrides
  @classmethod
  def can_create_key(cls, key):
    return Issue.can('update', key.parent())

  @classmethod
  def can_update_key(cls, key):
    return Issue.can('update', key.parent())

  def can_read(self):
    return self.root_async.get_result().can('read')

  #### Model overrides
  def to_dict(self, include=None, exclude=None):
    exclude = set(exclude or ())
    exclude.add('hidden')
    exclude.add('raw_comments')
    r = super(Patchset, self).to_dict(include, exclude)
    if not include or 'comments' in include:
      r['comments'] = [c.to_dict() for c in self.comments.itervalues()]
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
  patchset = ndb.IntegerProperty(indexed=False)
  modified = ndb.DateTimeProperty(auto_now=True)

  deleted = ndb.BooleanProperty(default=False)

  def to_dict(self, include=None, exclude=None):
    exclude = set(exclude or ())
    exclude.add('deleted')
    return super(DraftComment, self).to_dict(include=include, exclude=exclude)


class IssueMetadata(authed_model.AuthedModel):
  raw_drafts = ndb.LocalStructuredProperty(DraftComment, repeated=True)

  had_updates = ndb.BooleanProperty()
  acknowledged_messages = ndb.IntegerProperty(repeated=True)
  ent_version = ndb.IntegerProperty()

  n_drafts = ndb.ComputedProperty(lambda self: len(self.raw_drafts))
  starred = ndb.BooleanProperty(default=False)

  #### API manipulation
  @ndb.tasklet
  def add_draft_async(self, patch_key, body, side, lineno):
    d_ent = DraftComment(
      patch=patch_key.id(),
      patchset=patch_key.parent().id(),

      body=body,
      lineno=lineno,
      side=side,
    )

    @ndb.non_transactional
    @ndb.tasklet
    def get_context():
      patchset = yield patch_key.parent().get_async()
      patches = yield patchset.patches_async
      patch = patches[patch_key.id()]
      content = getattr(patch, side)  # old or new
      raise ndb.Return((yield content.lines_async)[lineno])
    # TODO(iannucci): Proper newline handling? is rstrip() good enough?
    d_ent.context_line = (yield get_context()).rstrip()

    d_ent.id = self.drafts.append(d_ent)
    raise ndb.Return(d_ent)

  def clear_drafts(self):
    self.raw_drafts = []
    self.drafts.clear()

  #### Properties
  @utils.cached_property
  def drafts(self):
    for i, d in enumerate(self.raw_drafts):
      d.id = i + 1
    return IDIndexedCollection(
      '$Drafts',
      ((None if d.deleted else d) for d in self.raw_drafts),
      self.key,
      self.raw_drafts.append
    )

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
    return cur_user and '<%s>' % cur_user.email() == key.parent().id()

  @classmethod
  def can_read_key(cls, key):
    return cls.can('create', key)

  @classmethod
  def can_update_key(cls, key):
    return cls.can('create', key)
