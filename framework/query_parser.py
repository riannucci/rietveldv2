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

import urllib

from google.appengine.ext import ndb

from . import exceptions

from .monkeypatch import prop_from_str  # pylint: disable=W0611


CLOSE_PAREN = object()


def _reversed_tokenize(string, token, convert=lambda x: x):
  # Adapted from http://stackoverflow.com/a/3862154
  word = []
  for c in reversed(string):
    if c == token:
      if word:
        yield convert(''.join(reversed(word)))
        word = []
    else:
      word.append(c)
  if word:
    yield convert(''.join(reversed(word)))


@ndb.tasklet
def _parse_comparison(model, field, op, values):
  # pylint: disable=W0212
  prop = model._properties[field]
  convert = prop._user_value_from_str_async
  if op == 'IN':
    assert len(values) >= 1
    raise ndb.Return(prop._IN((yield map(convert, values))))
  else:
    assert len(values) == 1
    raise ndb.Return(prop._comparison(op, (yield convert(values[0]))))


@ndb.tasklet
def _query_junction_async(op, node_asyncs):
  assert op in ('AND', 'OR')
  raise ndb.Return(getattr(ndb, op)(*(yield node_asyncs)))


class StringQueryMixin(object):
  @classmethod
  def parse_query_async(cls, query_string, ancestor=None):
    stack = []
    token_accumulator = []

    orders = []

    for token in _reversed_tokenize(query_string, ',', urllib.unquote_plus):
      splitted = token.rsplit(':', 1)
      if len(splitted) > 1 and splitted[1] != '': # empty op is 'quote'
        token, op = splitted
        if op in ('!=', '==', '<', '>', '<=', '>=', 'IN'):
          stack.append(_parse_comparison(cls, token, op, token_accumulator))
        elif op == 'ORDER':
          order_ops = []
          for field in token_accumulator:
            neg = field[0] == '-'
            if field[0] in '-+':
              field = field[1:]
            prop = cls._properties[field]  # pylint: disable=W0212
            order_ops.append(prop.__neg__() if neg else prop.__pos__())
          assert order_ops
          orders += order_ops
        elif op == ')':
          stack.append(CLOSE_PAREN)
        elif op in ('OR(', 'AND('):
          assert not token_accumulator
          nodes = []
          tok = None
          while True:
            tok = stack.pop()
            if tok is CLOSE_PAREN:
              break
            nodes.append(tok)
          assert len(nodes) > 1
          stack.append(_query_junction_async(op[:-1], nodes))
        else:
          raise exceptions.BadData('Unknown operator %r' % op)
        token_accumulator = []
      else:
        token_accumulator.append(token)

    assert len(token_accumulator) == 0
    query = cls.query(*(yield stack), ancestor=ancestor)
    q_forward = query.order(*orders)
    q_backward = query.order(o.reversed() for o in orders)
    raise ndb.Return((q_forward, q_backward))
