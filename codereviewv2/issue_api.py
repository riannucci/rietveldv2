from google.appengine.ext import ndb

from .. import cas
from ..framework import middleware, handler, exceptions

from . import models

STATUS_CODE = middleware.JSONMiddleware.STATUS_CODE
API_PREFIX = '^api/v2/'


class Issues(handler.RESTCollectionHandler):
  PREFIX = API_PREFIX+r'issues/'

  # TODO(iannucci): Implement get?

  @ndb.synctasklet
  def post(self, request):
    """Creates an issue.

    Expects a JSON body with the following schema:
    {
      'reviewers': [<email>*],
      'cc': [<email>*],
      'send_message': <bool>,
      'subject': <str>,
      'description': <str>,
      'repo_url': <str>,
      'private': <bool>,
      'patchset': <CASEntry id>,
    }

    Returns status JSON with additional data on success:
    { 'issue_id': <id for new Issue> }
    """
    data = request.json

    patchset_cas = cas.models.CAS_ID.fromdict(data['patchset'])
    if not data['cc'] and not data['reviewers'] and data['send_message']:
      raise exceptions.BadData('Cannot send_message with no-one to send to.')

    issue = yield models.Issue.create_async(
        data['subject'], data['description'], data['cc'], data['reviewers'],
        data['private']
    )
    ps = issue.add_patchset_async(patchset_cas)
    m = models.NONE_FUTURE
    if data['send_message']:
      m = issue.add_message_async(patchset=issue.patchsets[0])
    yield ps, m

    yield issue.flush_to_ds_async()

    raise ndb.Return({
      STATUS_CODE: 201,
      'issue_id': issue.key.id()})


class Issue(handler.RESTItemHandler):
  PARENT = Issues

  def get(self, request):
    return request.issue_async().get_result().to_dict()

  @ndb.synctasklet
  def put(self, request):
    """Edits an Issue.

    Expects a JSON body with the following schema. Each key is optional:
    {
      'reviewers': [<email>*],
      'cc': [<email>*],
      'subject': <str>,
      'description': <str>,
      'private': <bool>,
      'closed': <bool>,
    }

    Returns status JSON.
    """
    issue = yield request.issue_async()
    issue.modify_from_dict(request.json)
    yield issue.flush_to_ds_async()


class PatchSets(handler.RESTCollectionHandler):
  PARENT = Issue

  @ndb.synctasklet
  def post(self, request):
    """Adds a patchset to an Issue.

    Expects a JSON body with the following schema. Each key is optional:
    {
      'patchset': <CASEntry id>,
      'message': <str>,
    }
    """
    cas_id = cas.models.CAS_ID.fromdict(request.json['patchset'])

    issue = yield request.issue_async()
    yield issue.add_patchset_async(cas_id, request.json['message'])
    yield issue.flush_to_ds_async()


class PatchSet(handler.RESTItemHandler):
  PARENT = PatchSets

  @ndb.synctasklet
  def delete(self, request):
    ps = yield request.patchset_async()
    yield ps.delete_async()
    yield ps.flush_to_ds_async()


class Patches(handler.RESTCollectionHandler):
  PARENT = PatchSet


class Patch(handler.RESTItemHandler):
  PARENT = Patches


class Comments(handler.RESTCollectionHandler):
  PARENT = Patch


class Messages(handler.RESTCollectionHandler):
  # post txn needs to span both Issue and Account.
  TRANSACTIONAL = ndb.transactional(  # pylint: disable=E1120
    xg=True, propagation=ndb.TransactionOptions.INDEPENDENT)
  PARENT = Issue

  @ndb.synctasklet
  def post(self, request):
    """Converts drafts to comments, and possibly sends a message.

    Expects a JSON body with the following schema.
    {
      'issue': {  # optional, same format as for edit_issue()
        'subject': <str>,
        'reviewers': [<email>*],
        'cc': [<email>*],
      },
      'send_message': <bool>,
      'message': <bool>,
    }

    Returns a status JSON.
    """
    account = yield models.Account.current_async
    drafts = yield account.drafts_for_async(request.issue_key)
    kill_em = [d.delete_async() for d in drafts]

    issue = yield request.issue_async
    yield (
      issue.add_message_async(message=request.json['message'],
                              comments=drafts),
      issue.modify_from_dict_async(request.json.get('issue', {}))
    )

    yield issue.flush_to_ds_async(), kill_em


class Message(handler.RESTItemHandler):
  PARENT = Messages


# diff (SxS) -> /diff/<pset>/path/to/file
# diff2 (SxS) -> /diff2/<pset1>:<pset2>/path/to/file(:path/to/file)?

class Diff2(handler.RequestHandler):
  MIDDLEWARE = [middleware.JSONMiddleware]
  ROUTES = {'issue_api_diff':
            Issue().PREFIX+'/diff2/([^:]*):([^/]*)/([^:]*)(?::(.*))?/?$'}

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





