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

"""API for the Content Addressed Store"""

from google.appengine.ext import ndb

from framework import rest_handler, utils
from framework.monkeypatch import fix_django_transfer_encoding   # pylint: disable=W0611

from cas import models

from . import common


class CASEntries(rest_handler.RESTCollectionHandler):
  PREFIX = common.API_PREFIX
  ID_TYPE = str
  ID_TYPE_TOKEN = models.CAS_ID.NON_CAPTURE_REGEX
  COLLECTION_NAME = 'cas_entries'
  MODEL_NAME = 'CASEntry'
  SPECIAL_ROUTES = {'lookup': 'lookup'}

  MAX_BUFFER_SIZE = 50 * 1024

  # TODO(iannucci): Implement getters? This requires CAS to know about
  # application-level ACLs per CAS entry.

  # TODO(iannucci): Implement application-defined per-account upload quotas

  @ndb.tasklet
  def get_lookup(self, _key, *cas_refs):
    # [ {csum:, content_type:, size:}]
    cas_refs = map(models.CAS_ID.from_dict, cas_refs)
    raise ndb.Return({
      ent.cas_id.csum: ent.to_dict(exclude=['csum'])
      for ent in (yield [x.entry_async() for x in cas_refs])
      if ent is not None
    })

  def put(self, _key, **all_data):
    # TODO(iannucci): This function isn't /really/ async... it would be nice
    # if ndb.tasklet had an Any object you could yield that knew how to wait
    # for a single Future.

    # expect request body to be a json blob:
    #  {
    #    'csum': {
    #      'data': base64(file data),
    #      'content_type': <content_type>,
    #      'charset': <charset>,  # optional
    #    }
    #  }
    ret = {}
    outstanding_futures = utils.IdentitySet()
    buffer_size = 0

    for csum in sorted(all_data.iterkeys()):  # for testing
      data_and_metadata = all_data.pop(csum)
      data = data_and_metadata.pop('data').decode('base64')
      size = len(data)
      content_type = data_and_metadata['content_type']

      while (buffer_size + size) >= self.MAX_BUFFER_SIZE:
        if not outstanding_futures:
          break  # current file is huge!
        f = ndb.Future.wait_any(outstanding_futures)
        ent = f.get_result()
        buffer_size -= ent.cas_id.size
        outstanding_futures.remove(f)
        ret[ent.cas_id.csum] = ent.to_dict(exclude=['csum'])

      buffer_size += size

      charset = None
      if content_type.startswith('text/'):
        charset = data_and_metadata.get('charset', 'ascii').lower()
      assert charset in (None, 'utf-8', 'ascii')
      cas_id = models.CAS_ID(csum.decode('hex'), size, content_type,
                             charset)
      outstanding_futures.add(cas_id.create_async(data))

    while outstanding_futures:
      f = ndb.Future.wait_any(outstanding_futures)
      ent = f.get_result()
      ret[ent.cas_id.csum] = ent.to_dict(exclude=['csum'])
      outstanding_futures.remove(f)

    return ret

  @rest_handler.skip_process_request
  @ndb.tasklet
  def post_one(self, key, request):
    ent = yield models.CAS_ID.from_key(key).create_async(request.read())
    raise ndb.Return(ent.to_dict())

