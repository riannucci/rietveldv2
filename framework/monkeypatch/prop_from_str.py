import base64
import datetime
import json

from google.appengine.ext import ndb

from .. import exceptions, utils

if not hasattr(ndb.Property, '_user_value_from_str_async'):
  def _blob_convert(val):
    splitted = val.rsplit('|', 1)
    if len(splitted):
      val, filt = splitted
      if filt == '|':
        pass
      elif filt == 'base64':
        val = base64.urlsafe_b64decode(val)
      else:
        raise exceptions.BadData('Unknown filter %r' % filt)
    return val

  strptime = datetime.datetime.strptime
  # TODO(iannucci): support GenericProperties, ComputedProperties
  to_patch = {
    ndb.BlobKeyProperty: ndb.BlobKey,
    ndb.BlobProperty: _blob_convert,
    ndb.BooleanProperty: lambda x: x.lower() == 'true',
    ndb.DateProperty: lambda x: strptime(x, '%d.%m.%Y').date(),
    ndb.DateTimeProperty: lambda x: strptime(x, '%d.%m.%Y %H:%M:%S'),
    ndb.FloatProperty: float,
    ndb.GeoPt: lambda x: ndb.GeoPt(*map(float, x.split(maxsplit=2))),
    ndb.IntegerProperty: int,
    ndb.JsonProperty: lambda s: json.loads(_blob_convert),
    ndb.KeyProperty: lambda x: ndb.Key(urlsafe=x),
    ndb.Property: lambda s: s,
    ndb.TimeProperty: lambda x: strptime(x, '%H:%M:%S').time(),
  }
  for cls, fn in to_patch.iteritems():
    # pylint: disable=W0212
    cls._user_value_from_str_async = (
      lambda self, x: utils.completed_future(fn(x))
    )
