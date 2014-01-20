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


import argparse
import sys

# Settings
# Look at:
  # <parent_dirs>/.codereviewrc
  # ~/.codereviewrc

def main():
  parser = argparse.ArgumentParser(
    description="Create Issues/Upload patchsets to codereview.")
  parser.add_argument('-s', '--server', metavar='SERVER', help=(
    'The server to upload to. The format is host[:port]. '
    'Defaults to "%default".'
  ))

  # Do oauth2 dance

  # Get URL-to-server
  # Accmulate Patchset (scm dependent)
  # Upload Patchset + Files
  # Either:
  #   Create New Issue
  # OR
  #   Attach Patchset to Issue
  print 'test 123'
  from third_party import requests
  print requests
  return 0


if __name__ == '__main__':
  sys.exit(main())