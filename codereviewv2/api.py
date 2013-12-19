from google.appengine.ext import ndb

from .. import cas
from ..framework import middleware, handler, exceptions, utils, rest_handler

from . import issue_models, auth_models, diff

STATUS_CODE = middleware.STATUS_CODE
API_PREFIX = 'api/v2/'


# COLLECTION = cls.__name__.lower()
# MODEL_NAME = MODEL_NAME or '$%s' cls.__name__

# special_one(pattern) -> /collection/<id>/<pattern>
# special_all(pattern) -> /collection/<pattern>
#  extra *args go between key and **kwargs

class Issues(handler.RequestHandler,
             rest_handler.QueryableCollectionMixin):
  PREFIX = API_PREFIX
  MODEL_NAME = 'Issue'
  ITEMS = int

  # read_all_async implemented by QueryableCollectionMixin

  @ndb.transactional_tasklet
  def create_async(self, _key, send_message=False, patchset=None, **data):
    patchset_cas = issue_models.get_cas_future(patchset)

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
      issue.add_patchset_async(patchset_cas),
      issue.add_message_async() if send_message else utils.NONE_FUTURE
    ]

    yield issue.flush_to_ds_async()
    raise ndb.Return({STATUS_CODE: 201, 'id': issue.key.id()})

  @ndb.tasklet
  def read_one_async(self, key):
    ent = yield key.get_async()
    raise ndb.Return(ent.to_dict())

  @ndb.transactional_tasklet
  def modify_one_async(self, key, **data):
    issue = yield key.get_async()
    yield issue.update_async(**data)
    yield issue.flush_to_ds_async()
    raise ndb.Return({})

  @ndb.transactional_tasklet
  def delete_one_async(self, key):
    issue = yield key.get_async()
    yield issue.delete_async(key)
    yield issue.flush_to_ds_async()
    raise ndb.Return({})


  # diff2 (SxS) -> /diff2/<pset1>:<pset2>/path/to/file(:path/to/file)?



class Drafts(handler.RequestHandler):
  PARENT = Issues
  ITEMS = int

  @ndb.tasklet
  def read_all_async(self, key):
    metadata_key = issue_models.Issue.metadata_key(key.parent())
    metadata = yield metadata_key.get_async()
    raise ndb.Return([d.to_dict() for d in metadata.drafts])

  @ndb.tasklet
  def read_one_async(self, key):
    metadata_key = issue_models.Issue.metadata_key(key.parent())
    metadata = yield metadata_key.get_async()
    raise ndb.Return(metadata.drafts[key.id()].to_dict())


class Patchsets(handler.RESTCollectionHandler,
                rest_handler.QueryableCollectionMixin):
  PARENT = Issues
  MODEL_NAME = 'Patchset'
  ITEMS = int

  @ndb.transactional_tasklet
  def create_async(self, key, patchset=None, message=None):
    patchset_cas = issue_models.get_cas_future(patchset)
    issue = yield key.parent().get_async()
    ps = yield issue.add_patchset_async(patchset_cas, message=message)
    yield issue.flush_to_ds_async()
    raise ndb.Return({STATUS_CODE: 201, 'id': ps.key.id()})

  @ndb.tasklet
  def read_one_async(self, key):
    ent = yield key.get_async()
    raise ndb.Return(ent.to_dict())

  @ndb.transactional_tasklet
  def delete_one_async(self, key):
    issue, ps = yield key.parent().get_async(), key.get_async()
    yield issue.del_patchset_async(ps)
    yield issue.flush_to_ds_async()
    raise ndb.Return({})




class Comments(handler.RequestHandler):
  PARENT = Patchsets
  ITEMS = int

  @ndb.tasklet
  def read_all_async(self, key):
    ps = yield key.parent().get_async()
    ret = [c.to_dict() for c in ps.comments]
    raise ndb.Return(ret)

  @ndb.tasklet
  def read_one_async(self, key):
    ps = yield key.parent().get_async()
    raise ndb.Return(ps.comments[key.id()].to_dict())


class Patches(handler.RequestHandler):
  PARENT = Patchsets
  ITEMS = int

  @ndb.tasklet
  def read_all_async(self, key):
    ps = yield key.parent().get_async()
    raise ndb.Return([p.to_dict() for p in (yield ps.patches_async)])

  @ndb.tasklet
  def read_one_async(self, key):
    ps = yield key.parent().get_async()
    patches = yield ps.patches_async
    raise ndb.Return(patches[key.id()].to_dict())

  @handler.special_one('diff')
  @ndb.tasklet
  def read_diff_async(self, key, mode='git'):
    patchset = yield key.parent()
    patches = yield patchset.patches_async
    patch = patches[key.id()]
    patch.generate_diff(mode)



class PatchComments(handler.RequestHandler):
  PARENT = Patches
  COLLECTION = 'comments'
  ITEMS = int

  @ndb.tasklet
  def read_all_async(self, key):
    patch_key = key.parent()
    ps = yield patch_key.parent().get_async()
    patches = yield ps.patches_async
    ret = [c.to_dict() for c in patches[key.id()].comments]
    raise ndb.Return(ret)

  @ndb.tasklet
  def read_one_async(self, key, **data):
    comments = yield self.read_all_async(key, **data)
    ret = next((x for x in comments if x['id'] == key.id()), None)
    if not ret or ret['patch'] != key.parent().id():
      raise exceptions.NotFound('Comment')
    raise ndb.Return(ret.to_dict())


class PatchDrafts(handler.RequestHandler):
  PARENT = Patches
  COLLECTION = 'drafts'
  ITEMS = int

  @ndb.tasklet
  def read_all_async(self, key):
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
  def read_one_async(self, key, **data):
    drafts = yield self.read_all_async(key, **data)
    ret = next((x for x in drafts if x['id'] == key.id()), None)
    if not ret:
      raise exceptions.NotFound('Draft')
    raise ndb.Return({'data': ret})


class Messages(handler.RESTCollectionHandler):
  PARENT = Issues
  ITEMS = int

  @ndb.transactional_tasklet(xg=True)  # pylint: disable=E1120
  def create_async(self, key, message='', subject='', send_message=True):
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

  def read_all_async(self, key):
    issue = yield key.parent().get_async()
    raise ndb.Return([m.to_dict() for m in (yield issue.messages_async)])

  def read_one_async(self, key):
    raise ndb.Return((yield key.get_async()).to_dict())


class Accounts(handler.RequestHandler):
  PREFIX = API_PREFIX
  MODEL_NAME = 'Account'
  ITEMS = str

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
      raise ndb.Return(account.to_dict(exclude=[
        'xsrf_secret', 'xsrf_secret_size', 'blocked'
      ]))
    else:
      raise ndb.Return(account.to_dict(include=[
        'user', 'nickname',
      ]))

  @handler.special_all('me')
  def read_me_async(self, _key):
    me = yield auth_models.current_account_async()
    raise ndb.Return(me.to_dict(exclude=[
      'xsrf_secret', 'xsrf_secret_size', 'blocked'
    ]))


# diff (SxS) -> /diff/<pset>/path/to/file
# diff2 (SxS) -> /diff2/<pset1>:<pset2>/path/to/file(:path/to/file)?

class Diff2(handler.RequestHandler):
  MIDDLEWARE = [middleware.JSONMiddleware]
  ROUTES = {'issue_api_diff':
            issue_models.Issue().PREFIX+'/diff2/([^:]*):([^/]*)/([^:]*)(?::(.*))?/?$'}

  @ndb.synctasklet
  def get(self, _request, left_id, right_id):
    # TODO(iannucci): use left/right files
    # TODO(iannucci): Cache popular diffs?
    left, right = yield (
      cas.models.CASEntry.get_by_csum_async(left_id),
      cas.models.CASEntry.get_by_csum_async(right_id)
    )

    if not left:
      exceptions.NotFound('Cannot find data for left side')
    if not right:
      exceptions.NotFound('Cannot find data for right side')

    if (left.cas_id.data_type != right.cas_id.data_type or
        not left.cas_id.data_type.startswith('text/')):
      raise exceptions.BadData('Undiffable CAS objects')

