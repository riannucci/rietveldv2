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
import re
import hashlib
import itertools
import logging

from google.appengine.ext import ndb

from . import types
from . import exceptions as cas_exceptions

from framework import utils, exceptions


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
      ret.append(ndb.Key(pairs=parent + [(klazz._get_kind(), 1)]))
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

  @utils.cached_property
  def cas_id(self):  # pylint: disable=E0202
    return CAS_ID.from_key(self.key)


class CAS_ID(object):
  # TODO/PERF(iannucci): Is hashlib the fastest on appengine? PyCrypto?
  HASH_ALGO = hashlib.sha256
  CSUM_SIZE = HASH_ALGO().digest_size
  REGEX = re.compile(
    r'([0-9a-fA-F]{%s}):(\d+):([^:]*)(?::(.*))?' % (CSUM_SIZE * 2))

  #### Constructors
  def __init__(self, csum, size, data_type, charset):
    assert csum and size and data_type

    assert len(csum) == self.CSUM_SIZE
    self.csum = csum.encode('hex')

    assert isinstance(size, int)
    self.size = size

    assert isinstance(data_type, basestring)
    self.data_type = data_type.lower()

    assert isinstance(charset, (basestring, type(None)))
    self.charset = charset.lower() if charset else None

  @classmethod
  def from_string(cls, string_id):
    match = cls.REGEX.match(string_id)
    assert match is not None
    csum = match.group(1).decode('hex')
    size = int(match.group(2))
    data_type = match.group(3)
    charset = match.group(4)
    return cls(csum, size, data_type, charset)

  @classmethod
  def from_key(cls, key):
    assert key.kind() == 'CASEntry'
    return cls.from_string(key.id())

  @classmethod
  def from_dict(cls, dct):
    try:
      csum, size, data_type = (dct['csum'].decode('hex'), dct['size'],
                               dct['data_type'])
      charset = dct.get('charset')
      return cls(csum, size, data_type, charset)
    except Exception:
      raise exceptions.BadData('Malformed CAS ID: %r' % dct)


  #### Data factory
  @ndb.transactional_tasklet(retries=0)  # pylint: disable=E1120
  def create_async(self, data, type_map=None):
    """Create a new, validated CASEntry object, and return an async_put for
    it.

    It is an error to create() an existing CASEntry.
    """
    assert isinstance(data, str)  # specifically want data to be raw bytes.

    if (yield self.key.get_async()) is not None:
      logging.error("Attempted to create('%s') which already exists" % self)
      raise exceptions.CASError("CASEntry('%s') already exists." % self)

    # TODO(iannucci): Implement delayed validation by storing the object as
    # an UnvalidatedCASEntry, and then fire a taskqueue task to promote it to
    # a CASEntry when it's ripe.
    yield self.verify_async(data, type_map)

    entry = CASEntry(key=self.key)

    yield [
      entry.put_async(),
      # TODO(iannucci): Implement other data storage methods.
      CASDataInline(parent=self.key, id=1, data=data).put_async()
    ]
    raise ndb.Return(entry)

  #### Data accessors
  def entry_async(self):
    return self.key.get_async()

  @utils.hybridmethod
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
    if len(data_objs) > 1:
      data_obj = sorted(data_objs, key=lambda x: x.timestamp)
    else:
      data_obj = data_objs[0]
    raise ndb.Return((yield data_obj.data_async))

  @utils.hybridmethod
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
    csum.update(self.data_type)
    if self.charset:
      csum.update(self.charset)
    csum = csum.hexdigest()
    if csum != self.csum:
      logging.warn('Checksum mismatch: %r v %r', csum, self.csum)
      raise cas_exceptions.CASValidationError('Checksum mismatch')

    if type_map is None:
      from . import default_data_types
      type_map = default_data_types.TYPE_MAP
    assert isinstance(type_map, types.CASTypeRegistry)

    return type_map.validate_async(data, self.data_type, self.charset,
                                   check_refs)

  @classmethod
  @ndb.tasklet
  def statuses_for(cls, cas_ids):
    """Returns a map of {status: [cas_id]}.

    status is one of "missing", "confirmed"

    Args:
      cas_id_iter - A non-blocking iterable of CAS_ID instances.
    """
    a, b = itertools.tee(cas_ids)
    ret = collections.defaultdict(list)

    ents = yield ndb.get_multi_async([str(cas_id) for cas_id in a])

    for cas_id, obj in itertools.izip(b, ents):
      key = 'missing' if obj is None else 'confirmed'
      ret[key].append(cas_id)
    raise ndb.Return(ret)

  #### Output conversion
  @utils.cached_property
  def key(self):
    return ndb.Key(CASEntry, str(self))

  def to_dict(self):
    r = {'csum': self.csum, 'size': self.size, 'data_type': self.data_type}
    if self.charset:
      r['charset'] = self.charset
    return r

  def __str__(self):
    fmt = "%(csum)s:%(size)s:%(data_type)s"
    if self.charset:
      fmt += ':%(charset)s'
    return fmt % self.to_dict()


class CAS_IDProperty(ndb.KeyProperty):
  # Pylint thinks this is an old-style class, but it's not really.
  # pylint: disable=E1002
  def __init__(self, data_type, charset=None, *args, **kwargs):
    kwargs['kind'] = CASEntry
    super(CAS_IDProperty, self).__init__(*args, **kwargs)
    self.data_type = data_type
    self.charset = utils.make_set(charset)

  def _to_base_type(self, value):
    assert isinstance(value, CAS_ID)
    assert value.data_type == self.data_type
    assert value.charset in self.charset
    return value.key

  def _from_base_type(self, value):
    return CAS_ID.from_key(value)
