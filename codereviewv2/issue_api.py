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
    ent = yield key.get_async()
    raise ndb.Return(ent.to_dict())

  # TODO(iannucci): Make this operation idempotent
  @ndb.transactional_tasklet
  def put_one(self, key, **data):
    issue = yield key.get_async()
    yield issue.update_async(**data)
    yield issue.flush_to_ds_async()
    raise ndb.Return({})

  @ndb.transactional_tasklet
  def delete_one(self, key):
    issue = yield key.get_async()
    yield issue.delete_async(key)
    yield issue.flush_to_ds_async()
    raise ndb.Return({})


class Drafts(rest_handler.RESTCollectionHandler):
  PARENT = Issues

  @ndb.tasklet
  def get(self, key):
    metadata_key = issue_models.Issue.metadata_key(key.parent())
    metadata = yield metadata_key.get_async()
    raise ndb.Return([d.to_dict() for d in metadata.drafts])

  @ndb.tasklet
  def get_one(self, key):
    metadata_key = issue_models.Issue.metadata_key(key.parent())
    metadata = yield metadata_key.get_async()
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
    issue, _ = yield key.parent().get_async()
    ps = yield issue.add_patchset_async(ownership_proof_fut, message=message)
    yield issue.flush_to_ds_async()
    raise ndb.Return({STATUS_CODE: 201, 'data': ps.to_dict()})

  @ndb.tasklet
  def get(self, key):
    issue = yield key.parent().get_async()
    psets = [x.to_dict() for x in (yield issue.patchsets_async)]
    raise ndb.Return(psets)

  @ndb.tasklet
  def get_one(self, key):
    ent = yield key.get_async()
    raise ndb.Return(ent.to_dict())

  @ndb.transactional_tasklet
  def delete_one(self, key):
    issue, ps = yield key.parent().get_async(), key.get_async()
    yield issue.del_patchset_async(ps)
    yield issue.flush_to_ds_async()
    raise ndb.Return({})

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
    patchset = yield key.get_async()
    patches = collections.deque((yield patchset.patches_async))

    # clear out the cached future, since the patchset will linger in the
    # ndb context, causing all the patch content data to stick around forever.
    del patchset.patches_async

    # TODO(iannucci): Convert to a real streaming response
    # TODO(iannucci): Cache generated diffs for some time period
    raise ndb.Return(HttpResponse(self._full_diff_generator(patches, mode),
                                  content_type='text/plain'))


class Comments(rest_handler.RESTCollectionHandler):
  PARENT = Patchsets

  @ndb.tasklet
  def get(self, key):
    ps = yield key.parent().get_async()
    ret = [c.to_dict() for c in ps.comments]
    raise ndb.Return(ret)

  @ndb.tasklet
  def get_one(self, key):
    ps = yield key.parent().get_async()
    raise ndb.Return(ps.comments[key.id()].to_dict())


class Patches(rest_handler.RESTCollectionHandler):
  PARENT = Patchsets

  SPECIAL_ROUTES = {
    'diff': r'diff',
    'diff2': r'diff/(\d+)/(\d+)'
  }

  @ndb.tasklet
  def get(self, key):
    ps = yield key.parent().get_async()
    raise ndb.Return([p.to_dict() for p in (yield ps.patches_async)])

  @ndb.tasklet
  def get_one(self, key):
    ps = yield key.parent().get_async()
    patches = yield ps.patches_async
    raise ndb.Return(patches[key.id()].to_dict())

  @ndb.tasklet
  def get_one_diff(self, key, mode='git'):
    patchset = yield key.parent()
    patches = yield patchset.patches_async
    patch = patches[key.id()]
    # TODO(iannucci): Convert to a real streaming response
    # TODO(iannucci): Cache generated diffs for some time period
    raise ndb.Return(HttpResponse(patch.generate_diff(mode),
                                  content_type='text/plain'))

  @ndb.tasklet
  def get_one_diff2(self, key, right_ps_id, right_p_id, mode='git'):
    issue_key = key.parent().parent()
    right_key = ndb.Key(pairs=issue_key.pairs() + [('Patchset', right_ps_id)])

    left_patchset, right_patchset = yield [
      key.parent().get_async(),
      right_key.get_async()
    ]
    left_patches, right_patches = yield [
      left_patchset.patches_async,
      right_patchset.patches_async
    ]
    left_patch, right_patch = left_patches[key.id()], right_patches[right_p_id]
    fake_patch = issue_models.Patch(None, left_patch.new, right_patch.new, None)

    # TODO(iannucci): Convert to a real streaming response
    # TODO(iannucci): Cache generated diffs for some time period
    raise ndb.Return(HttpResponse(fake_patch.generate_diff(mode),
                                  content_type='text/plain'))


class PatchComments(rest_handler.RESTCollectionHandler):
  PARENT = Patches
  COLLECTION_NAME = 'comments'

  @ndb.tasklet
  def get(self, key):
    patch_key = key.parent()
    ps = yield patch_key.parent().get_async()
    patches = yield ps.patches_async
    ret = [c.to_dict() for c in patches[key.id()].comments]
    raise ndb.Return(ret)

  @ndb.tasklet
  def get_one(self, key, **data):
    comments = yield self.get_async(key, **data)
    ret = next((x for x in comments if x['id'] == key.id()), None)
    if not ret or ret['patch'] != key.parent().id():
      raise exceptions.NotFound('Comment')
    raise ndb.Return(ret.to_dict())


class PatchDrafts(rest_handler.RESTCollectionHandler):
  PARENT = Patches
  COLLECTION_NAME = 'drafts'

  @ndb.tasklet
  def get(self, key):
    patch_key = key.parent()
    patchset_key = patch_key.parent()
    metadata_key = issue_models.Issue.metadata_key(patchset_key.parent())
    metadata = yield metadata_key.get_async()
    ret = []
    for draft in metadata.drafts:
      if draft.patchset != patchset_key.id() or draft.patch != patch_key.id():
        continue
      ret.append(draft.to_dict())
    raise ndb.Return({'data': ret})

  @ndb.tasklet
  def get_one(self, key, **data):
    drafts = yield self.get_async(key, **data)
    ret = next((x for x in drafts if x['id'] == key.id()), None)
    if not ret:
      raise exceptions.NotFound('Draft')
    raise ndb.Return({'data': ret})

  def post(self, key, draft):
    issue_key = key.parent().parent()
    mdata_key = issue_models.Issue.metadata_key(issue_key).get_async()
    return issue_models.IssueMetadata.update_async(mdata_key, [draft])


class Messages(rest_handler.RESTCollectionHandler):
  PARENT = Issues

  @ndb.transactional_tasklet(xg=True)  # pylint: disable=E1120
  def post(self, key, message='', subject='', send_message=True):
    issue_key = key.parent()
    issue, metadata = yield [
      issue_key.get_async(),
      issue_models.Issue.metadata_key(issue_key).get_async(),
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
    issue = yield key.parent().get_async()
    raise ndb.Return([m.to_dict() for m in (yield issue.messages_async)])

  def get_one(self, key):
    raise ndb.Return((yield key.get_async()).to_dict())
