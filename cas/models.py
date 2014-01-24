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

import hashlib
import hmac
import logging
import os
import re

from google.appengine.ext import ndb

from . import types
from . import exceptions as cas_exceptions

from framework import utils, exceptions, xsrf


class CASData(ndb.Model):
  # NOTE: This could have been a PolyModel, but that doesn't allow
  # per-model control over cache/memcache behavior. In particular, we don't
  # want CASDataInline objects showing up in cache or memcache.

  # pylint: disable=R0922
  timestamp = ndb.DateTimeProperty(auto_now_add=True)

  @utils.cached_property
  def data_async(self):
    raise NotImplementedError()

  @staticmethod
  def keys(parent):
    # TODO(iannucci): support second-order subclasses
    parent = parent.pairs()
    ret = []
    for klazz in CASData.__subclasses__():
      # pylint: disable=W0212
      ret.append(ndb.Key(pairs=parent + ((klazz._get_kind(), 1),)))
    return ret


# TODO(iannucci): Implement other data storage methods.
class CASDataInline(CASData):
  _use_cache = False  # will blow out frontend memory
  _use_memcache = False  # will evict other, more-useful stuff

  data = ndb.BlobProperty(compressed=True)

  @utils.cached_property
  def data_async(self):
    return utils.completed_future(self.data)


class CASEntry(ndb.Model):
  generation = ndb.IntegerProperty(default=0)

  salt = ndb.BlobProperty()
  salt_hash = ndb.BlobProperty()

  git_hash = ndb.BlobProperty()

  @utils.cached_property
  def cas_id(self):  # pylint: disable=E0202
    return CAS_ID.from_key(self.key)

  def prove_ownership(self, proof):
    # TODO(iannucci): Make async
    expected = hmac.new(xsrf.assert_xsrf(), self.salt_hash,
                        CAS_ID.HASH_ALGO).hexdigest()
    return utils.constant_time_equals(proof, expected)

  def to_dict(self, exclude=()):
    ret = self.cas_id.to_dict(exclude)
    ret['salt'] = self.salt.encode('base64')[:-1]  # skip trailing newline
    return ret


class CAS_ID(object):
  # TODO/PERF(iannucci): Is hashlib the fastest on appengine? PyCrypto?
  HASH_ALGO = hashlib.sha256
  CSUM_SIZE = HASH_ALGO().digest_size
  REGEX = re.compile(
    r'([0-9a-fA-F]{%s}):(\d+):([^:]*)(?::(.*))?' % (CSUM_SIZE * 2))
  NON_CAPTURE_REGEX = re.sub(r'\(([^?])', r'(?:\1', REGEX.pattern)

  #### Constructors
  def __init__(self, csum, size, content_type, charset):
    assert csum and size and content_type

    assert len(csum) == self.CSUM_SIZE
    self.csum = csum.encode('hex')

    assert isinstance(size, int)
    self.size = size

    assert isinstance(content_type, basestring)
    self.content_type = content_type.lower()

    assert isinstance(charset, (basestring, type(None)))
    self.charset = charset.lower() if charset else None

    self.children_proven = False
    self.proven = False

  @classmethod
  def from_string(cls, string_id):
    match = cls.REGEX.match(string_id)
    assert match is not None
    csum = match.group(1).decode('hex')
    size = int(match.group(2))
    content_type = match.group(3)
    charset = match.group(4)
    return cls(csum, size, content_type, charset)

  @classmethod
  def from_key(cls, key):
    assert key.kind() == 'CASEntry'
    return cls.from_string(key.id())

  @classmethod
  def from_dict(cls, dct):
    try:
      csum, size, content_type = (
        dct['csum'].decode('hex'), dct['size'], dct['content_type'])
      charset = dct.get('charset')
      return cls(csum, size, content_type, charset)
    except Exception:
      raise exceptions.BadData('Malformed CAS ID: %r' % dct)


  #### Data factory
  def create_async(self, data, type_map=None):
    """Create a new, validated CASEntry object, and return an async_put for
    it.

    It is an error to create() an existing CASEntry.
    """
    assert isinstance(data, str)  # specifically want data to be raw bytes.
    salt = os.urandom(32)  # allocate early and outside txn, so that the tests
                           # are deterministic

    @ndb.transactional_tasklet(retries=0)  # pylint: disable=E1120
    def txn():
      if (yield self.key.get_async()) is not None:
        logging.error("Attempted to create('%s') which already exists" % self)
        raise exceptions.CASError("CASEntry('%s') already exists." % self)

      # TODO(iannucci): Implement delayed validation by storing the object as
      # an UnvalidatedCASEntry, and then fire a taskqueue task to promote it to
      # a CASEntry when it's ripe.
      yield self.verify_async(data, type_map)

      # TODO(iannucci): async versions of hmac/checksum
      salt_hash = hmac.new(salt, data, self.HASH_ALGO)
      git_hash = hashlib.sha1('blob %d\0' % len(data))
      git_hash.update(data)

      entry = CASEntry(key=self.key, salt=salt, salt_hash=salt_hash.digest(),
                       git_hash=git_hash.digest())

      yield [
        entry.put_async(),
        # TODO(iannucci): Implement other data storage methods.
        CASDataInline(parent=self.key, id=1, data=data).put_async()
      ]
      raise ndb.Return(entry)

    return txn()

  #### Data accessors
  def entry_async(self):
    return self.key.get_async()

  @ndb.tasklet
  def raw_data_async(self):
    """Retrieves the actual Data entity for this CASEntry.

    This is defined as the CASData decendant with an id of 1. This may be:
      * CASDataInline
    """
    # TODO(iannucci): When we implement other data methods, perhaps we should
    # just do a Future.wait_any and pick up the first non-None entry?
    # Alternately, we could store the actual key for the CASData subclass, but
    # then we wouldn't be able to know where the data is stored without looking
    # up the CASEntry first (right now we can run this method without loading
    # the CASEntry first).
    data_keys = CASData.keys(self.key)
    data_objs = filter(bool, (yield ndb.get_multi_async(data_keys)))
    if not data_objs:
      raise exceptions.NotFound(self.key)
    elif len(data_objs) > 1:
      data_obj = sorted(data_objs, key=lambda x: x.timestamp)
    else:
      data_obj = data_objs[0]
    raise ndb.Return((yield data_obj.data_async))

  @ndb.tasklet
  def data_async(self, type_map=None):
    data = yield self.raw_data_async()
    raise ndb.Return(
      (yield self.verify_async(data, type_map, check_refs=False))
    )

  #### Helpers
  def verify_async(self, data, type_map, check_refs=True):
    # TODO(iannucci): Verify hash in parallel?
    csum = self.HASH_ALGO(data)
    csum.update(str(len(data)))
    csum.update(self.content_type)
    if self.charset:
      csum.update(self.charset)
    csum = csum.hexdigest()
    if csum != self.csum:
      logging.warn('Checksum mismatch: %r v %r', csum, self.csum)
      raise cas_exceptions.CASValidationError('Checksum mismatch')

    if type_map is None:
      from . import default_content_types
      type_map = default_content_types.TYPE_MAP
    assert isinstance(type_map, types.CASTypeRegistry)

    return type_map.validate_async(data, self.content_type, self.charset,
                                   check_refs)

  @ndb.non_transactional
  @ndb.tasklet
  def prove_async(self, proofs, action, include_self=False):
    if include_self:
      data, entry = yield (self.data_async(), self.entry_async())
      refs = [x.entry_async() for x in data.CAS_REFERENCES] + [entry]
    else:
      data = yield self.data_async()
      refs = [x.entry_async() for x in data.CAS_REFERENCES]

    for cas_entry in (yield refs):
      csum = cas_entry.cas_id.csum
      # TODO(iannucci): This could be parallelized if prove_ownership was async
      if not cas_entry.prove_ownership(proofs[csum]):
        raise exceptions.Forbidden(
          '%s due to insufficient proof for %s' % (action, csum))

    self.children_proven = True
    self.proven = include_self
    raise ndb.Return(self)

  #### Output conversion
  @utils.cached_property
  def key(self):
    return ndb.Key(CASEntry, str(self))

  def to_dict(self, exclude=()):
    r = {'csum': self.csum, 'size': self.size,
         'content_type': self.content_type}
    if self.charset:
      r['charset'] = self.charset
    for key in exclude:
      r.pop(key, None)
    return r

  def __str__(self):
    fmt = "%(csum)s:%(size)s:%(content_type)s"
    if self.charset:
      fmt += ':%(charset)s'
    return fmt % self.to_dict()


class CAS_IDProperty(ndb.KeyProperty):
  # Pylint thinks this is an old-style class, but it's not really.
  # pylint: disable=E1002
  def __init__(self, content_type, charset=None, *args, **kwargs):
    kwargs['kind'] = CASEntry
    super(CAS_IDProperty, self).__init__(*args, **kwargs)
    self.content_type = content_type
    self.charset = utils.make_set(charset)

  def _to_base_type(self, value):
    assert isinstance(value, CAS_ID)
    assert value.content_type == self.content_type
    assert value.charset in self.charset
    return value.key

  def _from_base_type(self, value):
    return CAS_ID.from_key(value)
