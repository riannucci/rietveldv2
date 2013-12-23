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



def GarbageCollectCASEntries(subref_lookup_map=None):
  """Runs a garbage collection pass.

  Args:
    subref_lookup_map - A dict of {data_type: (f(data) -> list(subrefs))}
  """
  subref_lookup_map = subref_lookup_map or {}
