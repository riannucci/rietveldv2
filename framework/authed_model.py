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

from google.appengine.ext import ndb

from . import exceptions
from . import utils

from .monkeypatch import query_hooks  # pylint: disable=W0611

class AuthedModel(ndb.Model):
  """Allows a derived model to automatically restrict its access permissions
  by implementind a few boolean methods.

  Methods to implement:
    @classmethod
    def can_create_key(cls, key): ...

    @classmethod
    def can_read_key(cls, key): ...

    @classmethod
    def can_update_key(cls, key): ...

    @classmethod
    def can_delete_key(cls, key): ...

    # Note that can_create(self) is non-sensical.

    def can_read(self): ...

    def can_update(self): ...

    def can_delete(self): ...

  Permissions are resolved in two phases:
    * Try the _key method on your model.
    * If that returns AuthedModel.Dunno, try the non-_key method on an instance.
      * If we have the instance readily available, use that.
      * If not, use key.get() (which should be cached by ndb)
        * This case will mostly be hit by .can('delete', key) which is used by
          the _pre_delete_hook.

  If all of your auth is taken care of by the _key methods, there will be no
  datastore accesses performed. It's LIKELY that ndb will have your model
  cached anyway, so hopefully the key.get() won't be too much of a bummer.

  Permissions are cached on a per-instance basis, so e.g. once can_read() is
  called on instance X, it will not be called again on the same instance.

  We considered adding a permissions cache akin to ndb's Context._cache, but
  it seemed likely that the value would be either uncached, and would have to be
  resolved with a key.get(), or it would be present, in which case the key
  would also be present in ndb's in-process cache.

  All class methods default to 'Dunno', and all instance methods default to
  'False' (i.e. no permissions by default).

  In addition to the hooks, AuthedModel also shims to_dict (requires 'read'),
  and populate (requires 'update').
  """
  Dunno = object()

  #### __init__
  def __init__(self, *args, **kwargs):
    super(AuthedModel, self).__init__(*args, **kwargs)
    self._permissions_cache = {}
    self._in_datastore = False

  #### Classmethod Stubs
  @classmethod
  def can_read_key(cls, _key):
    return cls.Dunno

  @classmethod
  def can_update_key(cls, _key):
    return cls.Dunno

  @classmethod
  def can_create_key(cls, _key):
    return cls.Dunno

  @classmethod
  def can_delete_key(cls, _key):
    return cls.Dunno

  #### Instance Stubs
  def can_read(self):
    return False

  def can_update(self):
    return False

  def can_delete(self):
    return False

  #### Methods
  @utils.hybridmethod
  def can((self, cls), perm, key=None, lazy=False):
    if self is not None:
      if perm in self._permissions_cache:
        return self._permissions_cache[perm]
      assert key is None
      key = self.key

    rslt = getattr(cls, 'can_%s_key' % perm)(key)
    if rslt is cls.Dunno and not lazy:
      if self is None:
        self = key.get()  # relies on ndb's cache to make this speedy
        assert isinstance(self, cls)  # that would be pretty weird
        if perm in self._permissions_cache:
          return self._permissions_cache[perm]
      rslt = getattr(self, 'can_%s' % perm)()
      assert rslt is not cls.Dunno
    if self is not None:
      self._permissions_cache[perm] = rslt
    return rslt

  @utils.hybridmethod
  def assert_can((self, cls), perm, **kwargs):
    if not (self or cls).can(perm, **kwargs):
      raise exceptions.Forbidden(perm)

  #### Overrides
  def to_dict(self, *args, **kwargs):
    self.assert_can('read')
    return super(AuthedModel, self).to_dict(*args, **kwargs)

  def populate(self, *args, **kwargs):
    self.assert_can('update')
    return super(AuthedModel, self).populate(*args, **kwargs)

  #### Hooks
  @classmethod
  def _pre_allocate_ids_hook(cls, size, max, parent):
    cls.assert_can('create', ndb.Key(parent.flat()+(cls._class_name(), None)))
    super(AuthedModel, cls)._pre_allocate_ids_hook(size, max, parent)

  @classmethod
  def _pre_delete_hook(cls, key):
    cls.assert_can('delete', key)
    super(AuthedModel, cls)._pre_delete_hook(key)

  def _pre_put_hook(self):
    self.assert_can('update' if self._in_datastore else 'create')
    super(AuthedModel, self)._pre_put_hook()

  @classmethod
  def _pre_get_hook(cls, key):
    # be lazy here, _post_get_hook might catch it.
    if cls.can('read', key, lazy=True) is False:
      raise exceptions.Forbidden('read')
    super(AuthedModel, cls)._pre_get_hook(key)

  @classmethod
  def _post_get_hook(cls, key, future):
    obj = future.get_result()  # could throw, but what else could we do?
    if obj is not None:
      obj._in_datastore = True  # pylint: disable=W0212
      obj.assert_can('read')
      super(AuthedModel, cls)._post_get_hook(key, future)

  def _post_query_filter(self):
    self._in_datastore = True
    return self.can('read') and super(AuthedModel, self)._post_query_filter()
