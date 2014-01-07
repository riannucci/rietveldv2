# TODO(iannucci): We should also fix context for post_put_hook, but I don't
# care about that, so I'll skip it for now.

from google.appengine.ext import ndb
from google.appengine.api import namespace_manager, datastore

if '__getattribute__' not in ndb.MetaModel.__dict__:
  def __getattribute__(cls, name):
    orig = super(ndb.MetaModel, cls).__getattribute__(name)
    if name == '_post_get_hook':
      # pylint: disable=W0212
      if not ndb.Model._is_default_hook(ndb.Model._default_post_get_hook, orig):
        bind_ctx = ndb.get_context()
        bind_namespace = namespace_manager.get_namespace()
        bind_ds_connection = datastore._GetConnection()

        @classmethod
        def wrapper(_cls, *args, **kwargs):
          save_ctx = ndb.get_context()
          save_namespace = namespace_manager.get_namespace()
          save_ds_connection = datastore._GetConnection()
          try:
            ndb.set_context(bind_ctx)
            if save_namespace != bind_namespace:
              namespace_manager.set_namespace(bind_namespace)
            if save_ds_connection is not bind_ds_connection:
              datastore._SetConnection(bind_ds_connection)

            return orig(*args, **kwargs)

          finally:
            ndb.set_context(save_ctx)
            if save_namespace != namespace_manager.get_namespace():
              namespace_manager.set_namespace(save_namespace)
            if save_ds_connection is not datastore._GetConnection():
              datastore._SetConnection(save_ds_connection)
        return wrapper.__get__(None, cls)
    return orig
  ndb.MetaModel.__getattribute__ = __getattribute__
