from google.appengine.ext import ndb

from . import exceptions
from . import handler
from . import middleware
from . import query_parser
from . import utils


class RESTHandler(handler.RequestHandler):
  PARENT = None
  PREFIX = None

  def __init__(self, *args, **kwargs):
    super(RESTHandler, self).__init__(*args, **kwargs)
    assert bool(self.PARENT) ^ bool(self.PREFIX)

    if self.PARENT:
      self.MIDDLEWARE = self.PARENT.MIDDLEWARE + self.MIDDLEWARE
    else:
      self.MIDDLEWARE = [middleware.MethodOverride(),
                         middleware.JSONMiddleware()]
      self.PREFIX = self.PARENT.PREFIX+self.url_token+'/'


    self.name = self.__class__.__name__
    self.ROUTES = [(self.name.lower()+'_api', self.PREFIX+'?$')]


class QueryableCollectionMixin(object):
  DEFAULT_PAGE_LIMIT = 20
  PAGE_LIMIT_MAX = 100

  def read_all_async(self, key, query_string=None, cursor=None, limit=None,
                     **data):
    assert not data
    limit = int(limit or DEFAULT_PAGE_LIMIT)
    assert limit < PAGE_LIMIT_MAX

    # pylint: disable=W0212
    if cursor is not None:
      cursor = ndb.Cursor(urlsafe=cursor)

    if query_string:
      query, _ = yield query_parser.parse_query_async(self.MODEL, query_string,
                                                      key.parent())
    else:
      query = self.MODEL.query()
    if query._needs_multi_query():
      query = query.order(self.MODEL.key)

    results, cursor, more = yield query.fetch_page_async(
        limit, start_cursor=cursor
    )

    ret = {'data': [x.to_dict() for x in results]}
    if more:
      ret['cursor'] = cursor.urlsafe()

    raise ndb.Return(ret)


class RESTCollectionHandler(_RESTHandler):

  @utils.cached_property
  def url_token(self):
    return self.name.lower()

  @utils.cached_property
  def model(self):
    return ndb.Model._kind_map[self.name[:-1]]  # pylint: disable=W0212

  #### handler methods

  @ndb.synctasklet
  def post(self, request):
    ent = yield self.model.create_async(request.json)
    raise ndb.Return({middleware.STATUS_CODE: 201, 'id': ent.key.id()})

  @ndb.synctasklet
  def put(self, request):
    pass

  @ndb.synctasklet
  def delete(self, request):
    pass


class RESTItemHandler(_RESTHandler):
  # pylint thinks that nothing uses this abstract class, but it's wrong.
  # pylint: disable=R0921

  @utils.cached_property
  def model(self):
    return ndb.Model._kind_map[self.name]  # pylint: disable=W0212

  @utils.cached_property
  def url_token(self):
    return r'(\d+)'


  #### Handlers
  @ndb.synctasklet
  def get(self, _request):
    raise ndb.Return({
      'data': (yield getattr(self, '%s_async' % self.name)).to_dict()
    })

  @ndb.synctasklet
  def put(self, request):
    entity.update_from_dict_async(request.entity_key, **request.json)

  # TODO(iannucci): make delete() conditionally appear if model implemets
  # delete_async.
  @ndb.synctasklet
  def delete(self, request):
    ps = yield request.patchset_async()
    yield ps.delete_async()
    yield ps.flush_to_ds_async()


def all_subclasses(cls):
  for c in cls.__subclassess__():
    yield c
    for sc in all_subclasses(c):
      yield sc


class RESTCollectionMixin(object):
  PARENT_ENDPOINT = None
  COLLECTION_NAME = ''

  @classmethod
  def create_key_async(cls, key, **data):
    pass
  _default_create_async = create_key_async

  @classmethod
  def read_key_async(cls, key, recurse=False):
    pass
  _default_read_async = read_key_async

  @classmethod
  def update_key_async(cls, key, **data):
    pass
  _default_update_async = update_key_async

  @classmethod
  def delete_key_async(cls, key):
    pass
  _default_delete_async = delete_key_async

  #### Helpers
  @classmethod
  def no_extra(cls, data):
    if data:
      raise exceptions.BadData('Got extra data: %r' % data)

  @classmethod
  def create_routes(cls, base_uri):
    mapping = {}
    for c in all_subclasses(cls):
      pass




# /issues
# /issues?q=
# /issues/<id>
#    metadata
# /issues/<id>/patchsets
#    GET - listing of all patchsets for this issue
# /issues/<id>/patchsets/<id>
#    GET - (r=patches)
# /issues/<id>/patchsets/<id>/patches/<id>
#    GET - (r=patches)
# /accounts
# /accounts/<id>/issue_metadata
# /accounts/<id>/issue_metadata/<id>
