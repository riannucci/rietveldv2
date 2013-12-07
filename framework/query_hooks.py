import functools

from google.appengine.ext import ndb

class ExcludeEntityFromQuery(Exception):
  pass

def monkeypatch():
  """Monkey-patches ndb.Model and ndb.ModelAdapter to provide ndb-style hooks
  for entities which have been retrieved via queries.

  Due to a 'feature' in ndb[1], Model._post_get_hook is not called for entites
  which have been retrieved as part of a query.

  Calling monkeypatch() will overwrite:
    * ndb.Context._update_cache_from_query_result
    * ndb.Model._post_query_filter  (True function)
    * ndb.Model._post_keys_only_query_hook  (null function)

  Now, whenever you make an ndb query, your respective hook will be called on
  the instance which would have been returned from the ndb Context cache. This
  means that if you have an entity which is returned by multiple queries, your
  hook could be called on the same instance more than once (assuming you're
  using ndb's in-process cache).

  _post_query_filter will let you filter the results of any Query. If your
  filter returns a Falsy value, the entity will be dropped from the query
  result. Use this power wisely.

  [1] https://code.google.com/p/appengine-ndb-experiment/issues/detail?id=211
  """
  if not hasattr(ndb.Model, '_post_query_filter'):
    # pylint: disable=W0212
    #   All kinds of 'access to protected members' in here.
    orig_process_results = ndb.query.datastore_query.Batch._process_results
    @functools.wraps(orig_process_results)
    def _process_results(self, results):
      ret = orig_process_results(results)
      new_ret = []
      if self._batch_shared.query_options.keys_only:
        for key in ret:
          modelclass = ndb.Model._kind_map.get(key.kind(), None)
          if modelclass._post_keys_only_query_filter(key):
            new_ret.append(key)
      else:
        options = self._batch_shared.query_options
        ctx = ndb.get_context()
        for ent in ret:
          # Convert ent to the instance in the current in-process context
          # This will end up caching it (twice for simple queries), but that
          # should be OK...
          ent = ctx._update_cache_from_query_result(ent, options)
          if ent is not None and ent._post_query_filter():
            new_ret.append(ent)
      return new_ret
    ndb.query.datastore_query.Batch._process_results = _process_results

    ndb.Model._post_query_filter = lambda self: True
    ndb.Model._post_keys_only_query_hook = lambda self, key: None
