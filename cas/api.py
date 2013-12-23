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

from . import models, common


class Entries(rest_handler.RESTCollectionHandler):
  PREFIX = common.API_PREFIX
  ID_TYPE = str
  ID_TYPE_TOKEN = models.CAS_ID.REGEX.pattern
  MODEL_NAME = 'CASEntry'
  PROCESS_REQUEST = lambda r: r
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
    outstanding_futures = utils.IdentitySet()
    buffer_size = 0
    for csum, uploadfile in request.FILES.iteritems():
      while (buffer_size + uploadfile.size) >= self.MAX_BUFFER_SIZE:
        if not outstanding_futures:
          break  # current file is huge!
        f = ndb.Future.wait_any(outstanding_futures)
        buffer_size -= f.get_result().cas_id.size
        outstanding_futures.remove(f)

      assert uploadfile.name == ''
      buffer_size += uploadfile.size
      cas_id = models.CAS_ID(
        csum.decode('hex'), uploadfile.size,
        "%s; %s" % (uploadfile.content_type, uploadfile.charset))
      outstanding_futures.add(cas_id.create_async(uploadfile.read()))

    return utils.NONE_FUTURE

  def post_one_async(self, key, request):
    return models.CAS_ID.fromkey(key).create_async(request.read())

