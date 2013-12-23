import collections
import functools
import json
import logging
import re
import types

from google.appengine.ext import ndb

from django.conf.urls.defaults import url

from . import xsrf, handler, middleware, query_parser, exceptions


class QueryableCollectionMixin(object):
  DEFAULT_PAGE_LIMIT = 20
  PAGE_LIMIT_MAX = 100

  def get_async(self, key, query_string=None, cursor=None, limit=None,
                    **data):
    model = ndb.Model._kind_map[self.MODEL_NAME]  # pylint: disable=W0212
    assert not data
    limit = int(limit or self.DEFAULT_PAGE_LIMIT)
    assert limit < self.PAGE_LIMIT_MAX

    if cursor is not None:
      cursor = ndb.Cursor(urlsafe=cursor)

    if query_string:
      query, _ = yield query_parser.parse_query_async(model, query_string,
                                                      key.parent())
    else:
      query = model.query()
    if query._needs_multi_query():  # pylint: disable=W0212
      query = query.order(model.key)

    results, cursor, more = yield query.fetch_page_async(
        limit, start_cursor=cursor
    )

    ret = {'data': [x.to_dict() for x in results]}
    if more:
      ret['cursor'] = cursor.urlsafe()

    raise ndb.Return(ret)


class KeyMiddleware(middleware.Middleware):
  def __init__(self, kind_tokens, skip_last=False):
    """
    @param kind_tokens: A list of (kind, conversion_fn)
      kind - The kind field for the given key entry
      conversion_fn - A method to convert the url string to the correct id
          format. This will usually be `str` or `int`.
    @param skip_last: Substitute None for the id on the last kind_token.
    """
    self.skip_last = skip_last
    self.kind_tokens = kind_tokens

  def pre(self, request, *args, **kwargs):
    pairs = []
    args = list(args)
    for i, (kind, conversion_fn) in enumerate(self.kind_tokens):
      if self.skip_last and i == len(self.kind_tokens) - 1:
        id = None
      else:
        id = conversion_fn(args.pop(0))
      pairs.append((kind, id))
    return ([ndb.Key(pairs=pairs) if pairs else None] + args), kwargs


def skip_xsrf(func):
  func.check_xsrf = False
  return func


def check_xsrf(func):
  func.check_xsrf = True
  return func


def default_process_request(request):
  # TODO(iannucci):  Check mimetype?
  ret = None
  try:
    ret = json.load(request)
  except:
    logging.exception('Bad JSON request body')

  if not ret or not isinstance(ret, dict):
    raise exceptions.BadData('Expected JSON object in request body.')
  return ret


class RESTCollectionHandler(object):
  COLLECTION_NAME = None
  ID_TYPE = int
  ID_TYPE_TOKEN = None
  MODEL_NAME = None
  PARENT = None
  PREFIX = ''
  SPECIAL_ROUTES = {}
  MIDDLEWARE = ()
  PROCESS_REQUEST = default_process_request

  # e.g. post_async, get_cool_hat_async
  # We reserve OPTIONS to implement automatic explorable API endpoint.
  # Install browsable single-doc javascript endpoint at the base of the api.
  _BASE_REGEX = (r'^(?P<method>post|get|put|delete|head)'
                 r'(?:_(?P<single>one))?(?:_(?P<route>%s))?_async$')

  @classmethod
  def collection_name(cls):
    return cls.COLLECTION_NAME or cls.__name__.lower()

  @classmethod
  def model_name(cls):
    # Return an impossible model name for those Collections which do not
    # define a MODEL_NAME.
    return cls.MODEL_NAME or ('$%s' % cls.__name__)

  @classmethod
  def id_type_token(cls):
    assert isinstance(cls.ID_TYPE_TOKEN, (types.NoneType, basestring))
    return '(%s)' % (cls.ID_TYPE_TOKEN or {
      int: r'\d+',
      str: r'[^/]+',
    }[cls.ID_TYPE])

  @classmethod
  def key_pairs(cls):
    ret = [] if cls.PARENT is None else cls.PARENT.key_pairs()
    ret.append((cls.model_name, cls.ID_TYPE))
    return ret

  @classmethod
  def url_patten(cls, single, route=None):
    if cls.PARENT is None:
      ret = '^' + cls.PREFIX
    else:
      ret = cls.PARENT.url_patten('one')
    ret += '/' + cls.collection_name()
    if single:
      ret += '/' + cls.id_type_token()
    if route:
      ret += '/' + cls.SPECIAL_ROUTES[route]
    return ret

  @classmethod
  def generate_urlpatterns(cls, *args, **kwargs):
    handlers = collections.defaultdict(
      lambda: collections.defaultdict(dict))

    regex = re.compile(cls._BASE_REGEX % '|'.join(cls.SPECIAL_ROUTES))

    for name, func in cls.__dict__.iteritems():
      m = regex.match(name)
      if m:
        g = m.groupdict()
        handlers[bool(g['single'])][g['route']][g['method']] = func

    inst = cls(*args, **kwargs)
    key_pairs = cls.key_pairs()
    ret = []
    for single in (False, True):
      for route in [None] + cls.SPECIAL_ROUTES.keys():
        method_funcs = handlers[single][route]
        if not method_funcs:
          continue

        mware = ((middleware.MethodOverride(),
                  KeyMiddleware(key_pairs, single)) +
                cls.MIDDLEWARE +
                (middleware.JSONResponseMiddleware(),))

        url_regex = cls.url_patten(single, route) + '$'
        name = 'api_%s%s%s' % (cls.collection_name(),
                               '_one' if single else '',
                               '_' + route if route else '')
        methods = {}
        for method_name, func in method_funcs.iteritems():
          @functools.wraps(func)
          @ndb.toplevel
          def handler_method(request, *args, **kwargs):
            assert not kwargs  # would be from django's url router, but we're
                            # usurping kwargs for processed request data
            if getattr(func, 'check_xsrf', (method_name != 'get')):
              xsrf.assert_xsrf()

            data = cls.PROCESS_REQUEST(request)
            if isinstance(data, dict):
              return func(inst, *args, **data).get_result()
            else:
              return func(inst, *(args + (data,))).get_result()
          methods[method_name] = handler_method
        ret.append(url(
          url_regex, handler.RequestHandler(mware, **methods), name=name))

    return ret


def leaf_subclasses(cls):
  subclasses = cls.__subclasses__()
  if subclasses == []:
    yield cls
  else:
    for subclass in subclasses:
      for leaf in leaf_subclasses(subclass):
        yield leaf

def generate_urlpatterns_for_all(*args, **kwargs):
  ret = []
  for klazz in leaf_subclasses(RESTCollectionHandler):
    ret.extend(klazz.generate_urlpatterns(*args, **kwargs))
  return ret
