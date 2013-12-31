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
  ID_TYPE_TOKEN = models.CAS_ID.REGEX.pattern
  COLLECTION_NAME = 'cas_entries'
  MODEL_NAME = 'CASEntry'
  PROCESS_REQUEST = lambda self, req: req
  SPECIAL_ROUTES = {'lookup': 'lookup'}

  MAX_BUFFER_SIZE = 5 * 1024 * 1024

  # TODO(iannucci): Implement getters? This requires CAS to know about
  # application-level ACLs per CAS entry.

  # TODO(iannucci): Implement application-defined per-account upload quotas

  def get_lookup_async(self):
    pass

  def put_async(self, _key, request):
    # expect request body to be in the format of:
    #  one formdata file entry per CASEntry
    #  the 'name' of that formdata file is the expected checksum
    #  the Content-Size of the file must be set correctly
    #  the Content-Type of the file must be set correctly
    ret = []
    outstanding_futures = utils.IdentitySet()
    buffer_size = 0

    # Trick Django into processing the request as a multipart post.
    request.method = 'POST'
    request._load_post_and_files()  # pylint: disable=W0212

    for csum, uploadfile in request.FILES.iteritems():
      while (buffer_size + uploadfile.size) >= self.MAX_BUFFER_SIZE:
        if not outstanding_futures:
          break  # current file is huge!
        f = ndb.Future.wait_any(outstanding_futures)
        cas_id = f.get_result().cas_id
        buffer_size -= cas_id.size
        outstanding_futures.remove(f)
        ret.append(cas_id.to_dict())

      assert uploadfile.name in ('', csum)
      buffer_size += uploadfile.size

      data_type = uploadfile.content_type.lower()
      charset = None
      if data_type.startswith('text/'):
        charset = uploadfile.charset.lower() if uploadfile.charset else 'ascii'
      assert charset in (None, 'utf-8', 'ascii')
      cas_id = models.CAS_ID(csum.decode('hex'), uploadfile.size, data_type,
                             uploadfile.charset)
      outstanding_futures.add(cas_id.create_async(uploadfile.read()))

    while outstanding_futures:
      f = ndb.Future.wait_any(outstanding_futures)
      ret.append(f.get_result().cas_id.to_dict())
      outstanding_futures.remove(f)

    return utils.completed_future(ret)

  @ndb.tasklet
  def post_one_async(self, key, request):
    ent = yield models.CAS_ID.fromkey(key).create_async(request.read())
    raise ndb.Return(ent.to_dict())

