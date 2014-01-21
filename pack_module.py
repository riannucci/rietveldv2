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

"""Generates a python executable zip-module from a python module dir."""

import compileall
import json
import os
import sys
import zipfile

SCRIPT_PATH = os.path.abspath(os.path.dirname(__file__))

def main():
  indir = sys.argv[1]
  outfile = sys.argv[2]

  # Only including the .pyc saves about 50% of the pyz size, but would make the
  # module unusable by other pythons. It also makes it harder to inspect/debug.
  ok_exts = set(('.pyc', '.py'))

  with open(outfile, 'w') as raw_file:
    raw_file.write('#!/usr/bin/env python\n')
    zf = zipfile.ZipFile(raw_file, mode='w',
                        compression=zipfile.ZIP_DEFLATED)
    with zf:
      compileall.compile_dir(indir, quiet=True)

      for basedir, _, files in os.walk(indir, followlinks=True):
        for fname in files:
          if os.path.splitext(fname)[-1] not in ok_exts:
            continue
          fs_path = os.path.join(basedir, fname)

          arcname = fs_path[len(indir)+1:]

          zf.write(fs_path, arcname)
  os.chmod(outfile, 0755)

if __name__ == '__main__':
  sys.exit(main())
