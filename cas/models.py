import collections
import hashlib
import itertools
import logging

from google.appengine.ext import ndb
from google.appengine.ext.ndb import polymodel

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

  #### Constructors
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

  #### Data accessors
  def entry_async(self):
    return self.key.get_async()

  def raw_data_async(self):
    return CASEntry.raw_data_async(self.key)

  def data_async(self, type_map=None):
    return CASEntry.data_async(self.key, type_map)

  #### Output conversion
  @utils.cached_property
  def key(self):
    return ndb.Key(CASEntry, str(self))

  def to_dict(self):
    return {'csum': self.csum.encode('hex'), 'size': self.size,
            'data_type': self.data_type}

  def __str__(self):
    return "%(csum)s:%(size)s:%(data_type)s" % self.__dict__


class CAS_IDProperty(ndb.KeyProperty):
  # Pylint thinks this is an old-style class, but it's not really.
  # pylint: disable=E1002
  def __init__(self, data_type, *args, **kwargs):
    kwargs['kind'] = CASEntry
    super(CAS_IDProperty, self).__init__(*args, **kwargs)
    self.data_type = data_type

  def _to_base_type(self, value):
    assert isinstance(value, CAS_ID)
    assert value.data_type == self.data_type
    return value.key

  def _from_base_type(self, value):
    return CAS_ID.fromkey(value)


class CASData(polymodel.PolyModel):
  # pylint: disable=R0922
  timestamp = ndb.DateTimeProperty(auto_now_add=True)

  @utils.cached_property
  def data_async(self):
    raise NotImplementedError()


class CASDataInline(CASData):
  data = ndb.BlobProperty(compressed=True)

  @utils.cached_property
  def data_async(self):
    return utils.completed_future(self.data)

# TODO(iannucci): Implement other data storage methods.


class CASEntry(ndb.Model):
  csum = ndb.ComputedProperty(lambda self: self.cas_id.csum)
  size = ndb.ComputedProperty(lambda self: self.cas_id.size)
  data_type = ndb.ComputedProperty(lambda self: self.cas_id.data_type)

  generation = ndb.IntegerProperty(default=0)

  @utils.cached_assignable_property
  def cas_id(self):  # pylint: disable=E0202
    return CAS_ID.fromkey(self.key())

  @staticmethod
  def _verify_async(data_type, data, type_map=None, check_refs=True):
    if type_map is None:
      from . import default_data_types
      type_map = default_data_types.TYPE_MAP
    assert isinstance(type_map, types.CASTypeRegistry)
    if data_type not in type_map:
      raise CASUnknownDataType(type_map, data_type)
    return type_map[data_type](data, check_refs=check_refs)

  @classmethod
  @ndb.transactional_tasklet(retries=0)  # pylint: disable=E1120
  def create_async(cls, csum, data, data_type, type_map=None):
    """Create a new, validated CASEntry object, and return an async_put for
    it.

    It is an error to create() an existing CASEntry.
    """
    cls.no_extra(data)
    # TODO(iannucci): Implement delayed validation by storing the object as
    # an UnvalidatedCASEntry, and then fire a taskqueue task to promote it to
    # a CASEntry when it's ripe.
    yield cls._verify_async(data_type, data, type_map)

    cas_id = CAS_ID.fromdata(data, data_type)
    if cas_id.csum != csum:
      logging.error("Expecting csum '%s' but got '%s'" % (csum, cas_id.csum))
      raise types.CASValidationError("Checksum mismatch")

    entry = CASEntry(id=str(cas_id))
    entry.cas_id = cas_id  # We already computed it, so don't don't waste it
    key = entry.key

    if key.get() is not None:
      logging.error("Attempted to create('%s') which already exists" % csum)
      raise common.CASError("CASEntry('%s') already exists." % csum)
    new_key, _ = yield [
      entry.put_async(),
      # TODO(iannucci): Implement other data storage methods.
      CASDataInline(parent=key, id=1, data=data).put_async()
    ]
    assert key == new_key
    raise ndb.Return(entry)

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

  @utils.hybridmethod
  @ndb.tasklet
  def raw_data_async((self, _cls), key=None):
    """Retrieves the actual Data entity for this CASEntry.

    This is defined as the CASData decendant with an id of 1. This may be:
      * CASDataInline
    """
    key = key or self.key
    data_key = ndb.Key(pairs=key.pairs() + ('CASData', 1))
    data_obj = yield data_key.get_async()
    raise ndb.Return((yield data_obj.data_async))

  @utils.hybridmethod
  @ndb.tasklet
  def data_async((self, cls), key=None, type_map=None):
    key = key or self.key
    data = yield cls.raw_data_async(key)
    cas_id = CAS_ID.fromkey(key)

    # pylint: disable=W0212
    ret = yield cls._verify_async(cas_id.data_type, data, type_map,
                                  check_refs=False)
    raise ndb.Return(ret)
