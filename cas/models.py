import collections
import hashlib
import itertools
import logging

from google.appengine.datastore import datastore_query
from google.appengine.ext import ndb

from . import common
from . import types

from ..framework import utils, exceptions

class CASUnknownDataType(common.CASError):
  def __init__(self, type_map, data_type):
    self.type_map = type_map
    self.data_type = data_type
    super(CASUnknownDataType, self).__init__(
      "Unknown data_type: %s" % data_type)


class CAS_ID(object):
  # TODO/PERF(iannucci): Is hashlib the fastest on appengine? PyCrypto?
  HASH_ALGO = hashlib.sha256
  CSUM_SIZE = HASH_ALGO().digest_size

  def __init__(self, csum, size, data_type):
    assert csum and size and data_type

    assert len(csum) == self.CSUM_SIZE
    self.csum = csum.encode('hex')

    assert isinstance(size, int)
    self.size = size

    assert isinstance(data_type, basestring)
    self.data_type = data_type

  @classmethod
  def fromstring(cls, string_id):
    csum, size, data_type = string_id.split(':', 2)
    csum = csum.decode('hex')
    size = int(size)
    return cls(csum, size, data_type)

  @classmethod
  def fromkey(cls, key):
    assert key.kind() == 'CASEntry'
    return cls.fromstring(key.id())

  @classmethod
  def fromdict(cls, dct):
    try:
      return cls(dct['csum'].decode('hex'), dct['size'], dct['data_type'])
    except Exception:
      raise exceptions.BadData('Malformed CAS ID: %r' % dct)

  @classmethod
  def fromdata(cls, data, data_type):
    csum = cls.HASH_ALGO(data)
    csum.update(str(len(data)))
    csum.update(data_type)
    return cls(csum.digest(), len(data), data_type)

  def as_key(self):
    return ndb.Key(CASEntry, str(self))

  def __str__(self):
    return "%(csum)s:%(size)s:%(data_type)s" % self.__dict__


# All CASData* classes must have at least a 'timestamp' ndb.Property, and a
# 'data' property (could be a python @property, or an ndb.Property).
#
# CASEntry promotes the most-recent CASData* entity which is a decendent of
# itself to be the 'current' data.
#
# The split is necessary, because there's no way to do an ndb.get_multi with
# keys_only=True, which means validating the presence of large numbers of
# CASEntry's with integrated data will load way too much data into the
# frontend.
class CASDataInline(ndb.Model):
  # Prevent this model from going into ndb's in-process cache
  _use_cache = False

  data = ndb.BlobProperty(compressed=True)
  timestamp = ndb.DateTimeProperty(auto_now_add=True)

# TODO(iannucci): Implement other data storage methods.


class CASEntry(ndb.Model):
  csum = ndb.ComputedProperty(lambda self: self.cas_id.csum)
  size = ndb.ComputedProperty(lambda self: self.cas_id.size)
  data_type = ndb.ComputedProperty(lambda self: self.cas_id.data_type)

  generation = ndb.IntegerProperty(default=0)

  @utils.cached_assignable_property
  def cas_id(self):  # pylint: disable=E0202
    return CAS_ID.fromkey(self.key())

  @classmethod
  def create(cls, csum, data, data_type, type_map=None):
    """Create a new, validated CASEntry object, and return an async_put for
    it.

    It is an error to create() an existing CASEntry.
    """
    # TODO(iannucci): Implement delayed validation by storing the object as
    # an UnvalidatedCASEntry, and then fire a taskqueue task to promote it to
    # a CASEntry when it's ripe.
    if type_map is None:
      from . import default_data_types
      type_map = default_data_types.TYPE_MAP
    assert isinstance(type_map, types.CASTypeRegistry)

    if data_type not in type_map:
      raise CASUnknownDataType(type_map, data_type)

    type_map[data_type](data)

    cas_id = CAS_ID.fromdata(data, data_type)
    if cas_id.csum != csum:
      logging.error("Expecting csum '%s' but got '%s'" % (csum, cas_id.csum))
      raise types.CASValidationError("Checksum mismatch")

    entry = CASEntry(id=str(cas_id))
    entry.cas_id = cas_id  # We already computed it, so don't don't waste it
    key = entry.key

    def txn():
      if key.get() is not None:
        logging.error("Attempted to create('%s') which already exists" % csum)
        raise common.CASError("CASEntry('%s') already exists." % csum)
      new_key = entry.put()
      assert key == new_key

      # TODO(iannucci): Implement other data storage methods.
      CASDataInline(parent=key, data=data).put()
      return entry
    return ndb.transaction_async(txn, retries=0)

  @classmethod
  def get_by_csum_async(cls, csum):
    return cls.query(cls.csum == csum).get_async()

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

  @utils.cached_property
  def data(self):
    """Retrieves the actual Data entity for this CASEntry.

    This is defined as the last-written decendant of this CASEntry. It will
    be one of:
      * CASDataInline

    All of these entities have a .data member which will be the actual data
    contained.
    """
    return (
      ndb.Query()
      .ancestor(self)
      .order(
        datastore_query.PropertyOrder('timestamp'),
        datastore_query.PropertyOrder.DESCENDING
      )
    ).fetch_async(1)
