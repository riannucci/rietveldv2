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

from google.appengine.ext import ndb
from django.http import HttpResponse

import cas

from framework import middleware, exceptions, utils, rest_handler

from . import issue_models
from .common import API_PREFIX

STATUS_CODE = middleware.STATUS_CODE


@ndb.tasklet
def safe_get(key):
  ent = yield key.get_async()
  if ent is None:
    raise exceptions.NotFound(key)
  raise ndb.Return(ent)


class Issues(rest_handler.RESTCollectionHandler,
             rest_handler.QueryableCollectionMixin):
  PREFIX = API_PREFIX
  MODEL_NAME = 'Issue'

  # get implemented by QueryableCollectionMixin

  @ndb.transactional_tasklet
  def post(self, _key, send_message=False, patchset=None, proofs=None, **data):
    pset_cas_id = cas.models.CAS_ID.from_dict(patchset)
    ownership_proof_fut = pset_cas_id.prove_async(proofs, 'create issue')

    issue = yield issue_models.Issue.create_async(**data)
    if not issue.cc and not issue.reviewers and send_message:
      raise exceptions.BadData('Cannot send_message with no-one to send to.')

    # TODO(iannucci): There is a race with the garbage collection here.
    # User -> CAS
    # (wait GC_CYCLE_TIME)
    # GC starts
    # User Issue.create
    # We fetch cas entry
    # GC collects cas entry
    # We commit broken Issue/Patchset
    #
    # This should be fixed by asserting that the patchset has at least
    # 2*GC duration minutes to live in add_patchset_async. If not, treat it
    # as if it's already collected.  This could probably be implemented in
    # CASEntry itself.
    yield [
      issue.add_patchset_async(ownership_proof_fut),
      issue.add_message_async() if send_message else utils.NONE_FUTURE
    ]

    yield issue.flush_to_ds_async()
    raise ndb.Return({STATUS_CODE: 201, 'data': issue.to_dict()})

  @ndb.tasklet
  def get_one(self, key):
    ent = yield safe_get(key)
    raise ndb.Return(ent.to_dict())

  # TODO(iannucci): Make this operation idempotent
  @ndb.transactional_tasklet
  def put_one(self, key, **data):
    issue = yield safe_get(key)
    yield issue.update_async(**data)
    yield issue.flush_to_ds_async()
    raise ndb.Return(issue.to_dict())

  @ndb.transactional_tasklet
  def delete_one(self, key):
    issue = yield safe_get(key)
    yield issue.delete_async()
    yield issue.flush_to_ds_async()


class Drafts(rest_handler.RESTCollectionHandler):
  PARENT = Issues

  @ndb.tasklet
  def get(self, key):
    metadata_key = issue_models.Issue.metadata_key(key.parent())
    metadata = yield safe_get(metadata_key)
    raise ndb.Return([d.to_dict() for d in metadata.drafts])

  @ndb.tasklet
  def get_one(self, key):
    metadata_key = issue_models.Issue.metadata_key(key.parent())
    metadata = yield safe_get(metadata_key)
    raise ndb.Return(metadata.drafts[key.id()].to_dict())


class Patchsets(rest_handler.RESTCollectionHandler):
  PARENT = Issues
  MODEL_NAME = 'Patchset'
  SPECIAL_ROUTES = {
    'diff': 'diff$'
  }

  BUFFER_LIMIT = 10 * 1024 * 1024  # only buffer up to 10MB of data at a time

  @ndb.transactional_tasklet
  def post(self, key, patchset=None, message=None, proofs=None):
    pset_cas_id = cas.models.CAS_ID.from_dict(patchset)
    ownership_proof_fut = pset_cas_id.prove_async(proofs, 'add patchset')
    issue, _ = yield safe_get(key.parent())
    ps = yield issue.add_patchset_async(ownership_proof_fut, message=message)
    yield issue.flush_to_ds_async()
    raise ndb.Return({STATUS_CODE: 201, 'data': ps.to_dict()})

  @ndb.tasklet
  def get(self, key):
    issue = yield safe_get(key.parent())
    psets = [x.to_dict() for x in (yield issue.patchsets_async).itervalues()]
    raise ndb.Return(psets)

  @ndb.tasklet
  def get_one(self, key):
    ent = yield safe_get(key)
    raise ndb.Return(ent.to_dict())

  @ndb.transactional_tasklet
  def delete_one(self, key):
    issue, ps = yield safe_get(key.parent()), safe_get(key)
    yield issue.del_patchset_async(ps)
    yield issue.flush_to_ds_async()

  @classmethod
  def _full_diff_generator(cls, patches, mode):
    preloading = collections.deque()
    while patches or preloading:
      if patches:
        preloading_size = sum(p.size for p in preloading)
        while patches and preloading_size < cls.BUFFER_LIMIT:
          p = patches.popleft()
          p.get_data_futures()  # starts preloading data
          preloading_size += p.size
          preloading.append(p)
      patch = preloading.popleft()
      for line in patch.generate_diff(mode):
        yield line

  @ndb.tasklet
  def get_one_diff(self, key, mode='git'):
    patchset = yield safe_get(key)
    patches = collections.deque((yield patchset.patches_async).itervalues())

    # clear out the cached future, since the patchset will linger in the
    # ndb context, causing all the patch content data to stick around forever.
    del patchset.patches_async

    # TODO(iannucci): Convert to a real streaming response
    # TODO(iannucci): Cache generated diffs for some time period
    raise ndb.Return(
      HttpResponse(''.join(self._full_diff_generator(patches, mode)),
                   content_type='text/plain'))


class Comments(rest_handler.RESTCollectionHandler):
  PARENT = Patchsets

  @ndb.tasklet
  def get(self, key):
    ps = yield safe_get(key.parent())
    ret = [c.to_dict() for c in ps.comments]
    raise ndb.Return(ret)

  @ndb.tasklet
  def get_one(self, key):
    ps = yield safe_get(key.parent())
    raise ndb.Return(ps.comments[key.id()].to_dict())


class Patches(rest_handler.RESTCollectionHandler):
  PARENT = Patchsets

  SPECIAL_ROUTES = {
    'diff': r'diff',
    'diff2': r'diff/(\d+)/(\d+)'
  }

  @ndb.tasklet
  def get(self, key):
    ps = yield safe_get(key.parent())
    raise ndb.Return([p.to_dict()
                      for p in (yield ps.patches_async).itervalues()])

  @ndb.tasklet
  def get_one(self, key):
    ps = yield safe_get(key.parent())
    patches = yield ps.patches_async
    raise ndb.Return(patches[key.id()].to_dict())

  @ndb.tasklet
  def get_one_diff(self, key, mode='git'):
    patchset = yield safe_get(key.parent())
    patches = yield patchset.patches_async
    patch = patches[key.id()]
    # TODO(iannucci): Convert to a real streaming response
    # TODO(iannucci): Cache generated diffs for some time period
    raise ndb.Return(
      HttpResponse(''.join(patch.generate_diff(mode)),
                   content_type='text/plain'))

  @ndb.tasklet
  def get_one_diff2(self, key, right_ps_id, right_p_id, mode='git'):
    issue_key = key.parent().parent()
    right_key = ndb.Key(pairs=issue_key.pairs() + [('Patchset', right_ps_id)])

    left_patchset, right_patchset = yield [
      safe_get(key.parent()),
      safe_get(right_key)
    ]
    left_patches, right_patches = yield [
      left_patchset.patches_async,
      right_patchset.patches_async
    ]
    left_patch, right_patch = left_patches[key.id()], right_patches[right_p_id]
    fake_patch = issue_models.Patch(None, left_patch.new, right_patch.new, None)

    # TODO(iannucci): Convert to a real streaming response
    # TODO(iannucci): Cache generated diffs for some time period
    raise ndb.Return(
      HttpResponse(''.join(fake_patch.generate_diff(mode)),
                   content_type='text/plain'))


class PatchComments(rest_handler.RESTCollectionHandler):
  PARENT = Patches
  COLLECTION_NAME = 'comments'

  @ndb.tasklet
  def get(self, key):
    patch_key = key.parent()
    ps = yield safe_get(patch_key.parent())
    patches = yield ps.patches_async
    ret = [c.to_dict() for c in patches[key.id()].comments]
    raise ndb.Return(ret)

  @ndb.tasklet
  def get_one(self, key, **data):
    comments = yield self.get(key, **data)
    raise ndb.Return(comments[key.id()].to_dict())


class PatchDrafts(rest_handler.RESTCollectionHandler):
  PARENT = Patches
  COLLECTION_NAME = 'drafts'

  @classmethod
  @ndb.tasklet
  def _lookup_mdata(cls, key):
    issue_key = ndb.Key(*key.pairs()[0])
    mdata = yield issue_models.Issue.key_metadata_async(issue_key)
    raise ndb.Return(mdata)

  @classmethod
  @ndb.tasklet
  def _lookup_mdata_draft(cls, key):
    mdata = yield cls._lookup_mdata(key)
    if key.id() not in mdata.drafts:
      raise exceptions.NotFound(key)
    raise ndb.Return((mdata, mdata.drafts[key.id()]))

  @ndb.tasklet
  def get(self, key):
    mdata = yield self._lookup_mdata(key)
    raise ndb.Return([d.to_dict() for d in mdata.drafts.values()])

  @ndb.tasklet
  def get_one(self, key):
    _mdata, draft = yield self._lookup_mdata_draft(key)
    raise ndb.Return(draft.to_dict())

  @ndb.transactional_tasklet
  def delete_one(self, key):
    mdata, draft = yield self._lookup_mdata_draft(key)
    draft.deleted = True
    yield mdata.put_async()

  @ndb.transactional_tasklet
  def put_one(self, key, body):
    mdata, draft = yield self._lookup_mdata_draft(key)
    draft.body = body
    yield mdata.put_async()
    raise ndb.Return(draft.to_dict())

  @ndb.transactional_tasklet
  def put(self, key, drafts):
    mdata = yield self._lookup_mdata(key)
    mdata.clear_drafts()
    drafts = yield [mdata.add_draft_async(key.parent(), **d) for d in drafts]
    yield mdata.put_async()
    raise ndb.Return([d.to_dict() for d in drafts])

  @ndb.transactional_tasklet
  def post(self, key, body, side, lineno):
    mdata = yield self._lookup_mdata(key)
    draft = yield mdata.add_draft_async(key.parent(), body, side, lineno)
    yield mdata.put_async()
    raise ndb.Return(draft.to_dict())


class Messages(rest_handler.RESTCollectionHandler):
  PARENT = Issues

  @ndb.transactional_tasklet(xg=True)  # pylint: disable=E1120
  def post(self, key, message='', subject='', send_message=True):
    issue_key = key.parent()
    issue, metadata = yield [
      safe_get(issue_key),
      safe_get(issue_models.Issue.metadata_key(issue_key)),
    ]
    drafts = metadata.drafts
    del metadata.drafts

    msg, _ = yield [
      issue.add_message_async(message, subject, comments=drafts,
                              send_message=send_message),
      issue.modify_from_dict_async(issue or {})
    ]
    yield issue.flush_to_ds_async(), metadata.put_async()
    raise ndb.Return({STATUS_CODE: 201, 'id': msg.key.id()})

  def get(self, key):
    issue = yield safe_get(key.parent())
    raise ndb.Return([m.to_dict() for m in (yield issue.messages_async)])

  def get_one(self, key):
    raise ndb.Return((yield safe_get(key)).to_dict())
