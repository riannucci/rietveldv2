from google.appengine.ext import ndb

from . import utils, authed_model


class HierarchyMixin(object):
  # These RELY on ndb's use_cache (the default). Otherwise they will cause
  # datastore operations AND will get wired to the wrong instances.
  @utils.cached_property
  def root_async(self):
    pairs = self.key.pairs()
    if len(pairs) == 1:
      return utils.completed_future(self)
    else:
      return ndb.Key(flat=pairs[0]).get_async()

  @utils.cached_property
  def parent_async(self):
    k = self.key.parent()
    if k is None:
      return utils.completed_future(self)
    else:
      return k.get_async()

  @utils.cached_property
  @ndb.tasklet
  def dirty_entities_async(self):
    root = yield self.root_async
    if self is root:
      raise ndb.Return({
        'write': utils.IdentitySet(),
        'delete': utils.IdentitySet(),
      })
    else:
      ret = yield root.dirty_entities_async
      raise ndb.Return(ret)

  @ndb.tasklet
  def mark_async(self, operation='write', meta=False):
    dirty = yield self.dirty_entities_async
    dirty[operation].add(self)

    if not meta:
      parent = yield self.parent_async
      if parent is not self:
        yield parent.mark_async(operation, meta)

  @ndb.tasklet
  def clear_marks_async(self):
    dirty = yield self.dirty_entities_async
    for dirty_set in dirty.values():
      dirty_set.clear()

  @ndb.tasklet
  def is_dirty_async(self):
    dirty = yield self.dirty_entities_async
    ndb.Return(any(self in dirty_set for dirty_set in dirty.values()))

  @ndb.tasklet
  def flush_to_ds_async(self):
    dirty = yield self.dirty_entities_async
    writes = ndb.put_multi_async(dirty['write'])
    deletes = ndb.delete_multi_async(dirty['delete'])
    yield writes, deletes
    yield self.clear_marks_async()


class HideableModelMixin(authed_model.AuthedModel, HierarchyMixin):
  hidden = ndb.BooleanProperty(default=False)

  def delete_async(self):
    self.hidden = True
    return self.mark_async()

  def query(self, *args, **kwargs):
    ret = super(HideableModelMixin, self).query(*args, **kwargs)
    return ret.filter(HideableModelMixin.hidden == False)

  @classmethod
  @ndb.tasklet
  def get_by_id_async(cls, *args, **kwargs):
    ret = yield super(HideableModelMixin, cls).get_by_id_async(*args, **kwargs)
    if ret is None or ret.hidden:
      raise ndb.Return(None)
    raise ndb.Return(ret)

  @classmethod
  def get_by_id(cls, *args, **kwargs):
    ret = super(HideableModelMixin, cls).get_by_id(*args, **kwargs)
    if ret is None or ret.hidden:
      return None
    return ret

  def _post_query_filter(self):
    return (
      not self.hidden and
      super(HideableModelMixin, self)._post_query_filter())

  def can_read(self):
    return super(HideableModelMixin, self).can_read() and not self.hidden
