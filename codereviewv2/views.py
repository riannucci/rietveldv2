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

import datetime
import os
import urllib

from google.appengine.ext import ndb

from django.core.urlresolvers import reverse

import jinja2

from framework import middleware, handler, exceptions, query_parser, utils
from framework.monkeytest import fake_time

from . import issue_models
from .auth_models import Account

STATUS_CODE = middleware.JSONMiddleware.STATUS_CODE

VIEW_PREFIX = ''

# TODO(iannucci): Use ModuleLoader and precompile templates.
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), 'templates')
JINJA_ENVIRONMENT = jinja2.Environment(
      loader=jinja2.FileSystemLoader(TEMPLATE_PATH),
      extensions=['jinja2.ext.autoescape'],
      autoescape=True)


class GlobalJinjaObject(object):
  def __init__(self, request):
    self.request = request

  @utils.cached_property
  def account(self):
    return Account.current_async().get_result()


JINJA_MIDDLEWARE = middleware.Jinja(JINJA_ENVIRONMENT, GlobalJinjaObject)

class MainHandler(handler.RequestHandler):
  MIDDLEWARE = [JINJA_MIDDLEWARE]
  ROUTES = [
    ('main_index', '^/?$'),
    ('all',        '^/all/?$'),
    ('mine',       '^/mine/?$'),
    ('user_view',  '^/user/(.*)/?$'),
  ]

  @ndb.tasklet
  def user_issues(self, account):
    me = yield Account.current_async()
    m = issue_models.Issue
    limit = 100

    my_drafts = utils.completed_future([])
    if account == me:
      my_drafts = issue_models.DraftComment.query(
        ancestor=ndb.Key(pairs=me.key)).fetch_async()

    incoming = (m.query()
                .filter(m.closed == False)
                .filter(m.reviewers == account.lower_email)
                .order(-m.modified)).fetch_async(limit)

    outgoing = (m.query()
                .filter(m.closed == False)
                .filter(m.owner == account.user)
                .order(-m.modified)).fetch_async(limit)

    last_week = fake_time.utcnow() - datetime.timedelta(days=7)
    closed = (m.query()
              .filter(m.closed == True)
              .filter(m.owner == account.user)
              .filter(m.modified > last_week)
              .order(-m.modified)).fetch_async(limit)

    cc_issues = (m.query()
                 .filter(m.closed == False)
                 .filter(m.cc == account.lower_email)
                 .order(-m.modified)).fetch_async()

    my_drafts, incoming, outgoing, closed, cc_issues = yield [
      my_drafts, incoming, outgoing, closed, cc_issues
    ]

    draft_keys = set(d.key for d in my_drafts)
    filt = lambda itm: itm.key not in draft_keys

    unsent = [i for i in outgoing if not i.n_messages]
    outgoing = [i for i in outgoing if i.n_messages]

    # TODO(iannucci): include query links for sections
    sections = [
      {'title': 'Incoming reviews', 'data': filter(filt, incoming)},
      {'title': 'Outgoing reviews', 'data': filter(filt, outgoing)},
      {'title': 'Unsent issues', 'data': filter(filt, unsent)},
      {'title': 'Issues CC\'d to', 'data': filter(filt, cc_issues)},
      {'title': 'Closed Recently', 'data': filter(filt, closed)},
    ]
    if my_drafts:
      sections.insert(0, {'title': 'Issues with drafts by me',
                          'data': my_drafts})

    raise ndb.Return(('user.html', {
      'sections': sections
    }))

  @ndb.synctasklet
  def get(self, request, *nick_or_email):
    route_name = request.route_name()
    if route_name == 'main_index':
      account = yield Account.current_async
      route_name = 'all' if account is None else 'mine'

    if route_name == 'all':
      query = 'closed:==,False,:ORDER,-modified'
      query_handler = handler.RequestHandler.handler_for('query')
      raise ndb.Return((yield query_handler.get_async(request, query)))

    if route_name == 'user_view':
      nick_or_email = urllib.unquote_plus(nick_or_email[0])
      account = yield Account.get_for_nick_or_email_async(nick_or_email)
      if account is None:
        raise exceptions.NotFound(nick_or_email)
    elif route_name == 'mine':
      account = yield Account.current_async  # cached, cached, baby!
      if account is None:
        raise exceptions.NeedsLogin()
    else:
      assert False, 'Unknown route %s' % route_name

    raise ndb.Return((yield self.user_issues(account)))


class QueryHandler(handler.RequestHandler):
  DEFAULT_PAGE_LIMIT = 20
  PAGE_LIMIT_MAX = 100
  ROUTES = [
    ('query', '^/query/(.*)$')
  ]

  @staticmethod
  @ndb.tasklet
  def get_user_metadata(batch, index, issue):
    metadata = yield issue.metadata_async
    metadata.has_updates_async()

    class MapResult(object):
      def __init__(self, issue, metadata):
        self.issue = issue
        self.metadata = metadata

      @staticmethod
      def get_cursor(reverse=False):
        if batch.more_results:
          c = batch.cursor(index)
          if reverse:
            c = c.reversed()
          return c.urlsafe()

    raise ndb.Return(MapResult(issue, metadata))

  @ndb.tasklet
  def get_async(self, request, query_string):
    issue = issue_models.Issue
    qfwd, qbak = yield query_parser.parse_query_async(issue, query_string)
    if qfwd._needs_multi_query():  # pylint: disable=W0212
      # Multi-queries can only produce cursors if (ultimately) ordered by key.
      qfwd = qfwd.order(issue.key)
      qbak = qfwd.order(-issue.key)

    limit = int(request.GET.get('limit', self.DEFAULT_PAGE_LIMIT))
    assert limit <= self.PAGE_LIMIT_MAX

    is_prev = request.GET.get('prev', False)
    cursor = request.GET.get('cursor', None)
    if cursor:
      cursor = ndb.Cursor(urlsafe=cursor)
      if is_prev:
        cursor = cursor.reversed()

    query = qbak if is_prev else qfwd
    data = query.map_async(QueryHandler.get_user_metadata,
                           pass_batch_into_callback=True,
                           start_cursor=cursor,
                           produce_cursors=True,
                           limit=limit + 1)

    def mklink(cursor):
      if cursor is not None:
        ctx = {'cursor': cursor}
        if limit != self.DEFAULT_PAGE_LIMIT:
          ctx['limit'] = str(limit)
        return '%s?%s' % (reverse('query', args=[query_string]),
                          urllib.urlencode(ctx))
      else:
        return None

    last = data[limit]
    raise ndb.Return('query.html', {
      'query': {
        'results': data[:limit],
        'link': {
          'prev': mklink(last.get_cursor(reverse=True) if is_prev else cursor),
          'next': mklink(cursor if is_prev else last.get_cursor(reverse=False))
        }
      }
    })

  def get(self, request, query_string):
    return self.get_async(request, query_string).get_result()


class IssueHandler(handler.RequestHandler):
  MIDDLEWARE = [JINJA_MIDDLEWARE, middleware.AsyncEntityLoader('Issue')]

  def get(self, request):
    pass


class DiffHandler(handler.RequestHandler):
  pass


class Diff2Handler(handler.RequestHandler):
  pass


class PatchHandler(handler.RequestHandler):
  pass


class AccountHandler(handler.RequestHandler):
  pass
