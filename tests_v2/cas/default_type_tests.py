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


def default_map():
  import urls  # pylint: disable=W0612
  import cas
  r = {}
  for (prefix, name), func in cas.default_data_types.TYPE_MAP.iteritems():
    r.setdefault(prefix, {})
    r[prefix][name] = func.__module__ + '.' + func.__name__
  return r


def GenTests():
  from tests_v2.support.test import BasicTest
  yield BasicTest(default_map)
