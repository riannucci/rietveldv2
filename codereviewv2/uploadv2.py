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

import collections

from google.appengine.ext import ndb

import cas

from . import decorators as deco

CodereviewCASTypeMap = cas.CASTypeRegistry(cas.DEFAULT_TYPE_MAP)

@CodereviewCASTypeMap('text/universal_diff+universal; charset=UTF-8')
def validate_universal_diff(data):
  data = CodereviewCASTypeMap['text/universal; charset=UTF-8'](data)
  raise NotImplementedError(validate_universal_diff)

@CodereviewCASTypeMap('application/patchset_manifest+json')
def validate_patchset_manifest(data):
  data = CodereviewCASTypeMap['application/json'](data)
  raise NotImplementedError(validate_patchset_manifest)

@deco.require_method('GET')
@deco.login_required
@deco.json_status_response
@deco.json_request
@ndb.toplevel
def uploadv2_content_lookup(request):
  """GET /uploadv2/content/lookup

  Enquires about the status of one or more CASEntry objects.

  Body:
    {
      "intend_to_upload": {
        "csum1": {"size": ..., "data_type": ...},
        "csum2": {"size": ..., "data_type": ...},
        "csum3": {"size": ..., "data_type": ...},
        ...
      }
    }

  OK Response:
    {
      "status": "OK",
      "missing": ["csum1", "csum3", ...],
      "confirmed": ["csum2", ...],
    }

  Bad Response:
    {
      "status": "ERROR",
      "msg": "..."
    }
  """
  intend_to_upload = request.json.pop('intend_to_upload')
  if request.json:
    return {
      '$code': 400,
      'msg': 'Unxepected data: %s' % request.json
    }

  statuses = cas.CASEntry.statuses_for(
    cas.CAS_ID(c, s, dt) for c, (s, dt) in intend_to_upload.items()
  ).get_result()

  ret = collections.defaultdict(list)
  for status, cas_ids in statuses.iteritems():
    ret[status] = [cid.csum for cid in cas_ids]
  return ret


@deco.require_method('POST')
@deco.login_required
@deco.json_status_response
@ndb.toplevel
def uploadv2_content(request):
  """POST /uploadv2/content.

  Creates one or more CASEntry objects.

  Body (multipart/form-data):
    file(s):
      Content-Type - The data_type of the CASEntry
      <data> - The data for the CASEntry
  """
  code = 400
  errors = {}
  for csum, uploaded_file in request.FILES:
    # TODO(iannucci): Create a more-flexible interface for huge files.
    if uploaded_file.size > (1023 * 1024):
      code = 413
      errors[csum] = "This content is too damn big (> 1023KB)"
      continue

    data = uploaded_file.read()
    data_type = uploaded_file.content_type
    try:
      cas.CASEntry.create(
        csum, data, data_type, type_map=CodereviewCASTypeMap)
    except Exception as e:
      errors[csum] = e.message
  if errors:
    return {
      'msg': 'One or more errors while adding content. See "errors".',
      'errors': errors,
      '$code': code,
    }
  else:
    return {}


@deco.require_method('POST')
@deco.login_required
@deco.json_status_response
@deco.json_request
@ndb.toplevel
def uploadv2_patchset_complete(request):
  raise NotImplementedError("nooo")

