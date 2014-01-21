# Copyright 2014 Google Inc.
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

TARGET = 'uploadv2.pyz'

def archive_contents():
  import zipfile
  with zipfile.ZipFile(TARGET) as zf:
    return zf.namelist()

def GenTests():
  # TODO(iannucci): Add 'test depentencies' so that this can be made a
  # dependency of all the tests in this folder, etc.
  # For now, though, this is fast enough to do in the actual test generator.
  import subprocess
  subprocess.check_call(['make', TARGET])

  from tests_v2.support.test import BasicTest
  yield BasicTest(archive_contents)

