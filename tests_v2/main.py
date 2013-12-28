#!/usr/bin/env python
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

import Queue
import coverage
import glob
import multiprocessing
import os
import shutil
import sys
import tempfile
import traceback
import atexit

SCRIPT = os.path.abspath(__file__)
TEST_ROOT_PATH = os.path.dirname(SCRIPT)
SRC_ROOT_PATH = os.path.dirname(os.path.dirname(SCRIPT))

CONFIG_FILE_TEMPLATE = """
[run]
parallel = True
data_file = %(coverage_base)s
include =
  %(root)s/*
omit =
  %(root)s/*/__init__.py
  %(root)s/codereview/*
  %(root)s/static/*
  %(root)s/templates/*
  %(root)s/tools/*
  %(root)s/mapreduce/*
  %(root)s/test*
"""

TMP_SUFFIX = '.rietveld_tests'
_TMP_DIR = None
def get_tmp_dir():
  global _TMP_DIR
  _TMP_DIR = os.environ.get('RIETVELD_TMP_DIR', None)
  if _TMP_DIR is None:
    _TMP_DIR = tempfile.mkdtemp(TMP_SUFFIX)
    atexit.register(shutil.rmtree, _TMP_DIR, ignore_errors=True)
    os.environ['RIETVELD_TMP_DIR'] = _TMP_DIR
  return _TMP_DIR


class TestFailed(Exception):
  pass

class TestAbort(Exception):
  def __init__(self, name):
    super(TestAbort, self).__init__(name, traceback.format_exc())


POLL_IVAL = 0.1


def TestRunner(diediedie, training, test_queue, result_queue):
  cov = coverage.coverage(config_file=os.environ['COVERAGE_PROCESS_START'])
  cov._warn_no_data = False  # pylint: disable=W0212
  cov.start()
  try:
    while not diediedie.is_set():
      try:
        test = test_queue.get(True, POLL_IVAL)
      except Queue.Empty:
        continue

      try:
        m = test.train if training else test.run
        rslt = m()
      except Exception as e:
        rslt = e, traceback.format_exc()
      result_queue.put((test.name, rslt))
      test_queue.task_done()
  finally:
    cov.stop()
    cov.save()


def TestResultProcessor(diediedie, result_queue):
  while not diediedie.is_set():
    try:
      result = result_queue.get(True, POLL_IVAL)
    except Queue.Empty:
      continue

    name, data = result
    print '%s: %r' % (name, data or 'OK')
    result_queue.task_done()


def run_all_tests():
  tmp_dir = get_tmp_dir()

  for f in glob.glob(os.path.join(tempfile.gettempdir(), '*%s' % TMP_SUFFIX)):
    if f == tmp_dir:
      continue
    print 'removing left-over temp dir %r' % f
    shutil.rmtree(f, ignore_errors=True)

  coverage_base = os.path.join(get_tmp_dir(), '.coverage')
  config_file = os.path.join(tmp_dir, 'coverage.cfg')
  usercustomize_file = os.path.join(tmp_dir, 'lib', 'python', 'site-packages',
                                    'usercustomize.py')

  train = '--train' in sys.argv

  bad = []
  try:
    with open(config_file, 'w') as f:
      f.write(CONFIG_FILE_TEMPLATE % {
        'root': SRC_ROOT_PATH,
        'coverage_base': coverage_base
      })

    os.makedirs(os.path.dirname(usercustomize_file))
    shutil.copyfile(os.path.join(TEST_ROOT_PATH, 'usercustomize.tpl'),
                    usercustomize_file)

    os.environ['PYTHONUSERBASE'] = tmp_dir
    os.environ['COVERAGE_PROCESS_START'] = config_file

    os.environ['DJANGO_SETTINGS_MODULE'] = 'settings'
    os.environ['SERVER_SOFTWARE'] = 'Dev-Testing'

    sys.path.insert(0, SRC_ROOT_PATH)
  except Exception:
    print 'Failed to set up environment!'
    print traceback.print_exc()
    return

  tests_run = 0
  try:
    diediedie = multiprocessing.Event()
    test_queue = multiprocessing.JoinableQueue()
    result_queue = multiprocessing.JoinableQueue()
    procs = [multiprocessing.Process(
        target=TestRunner, args=(diediedie, train, test_queue, result_queue))
      for _ in xrange(multiprocessing.cpu_count())
    ]
    processor = multiprocessing.Process(target=TestResultProcessor,
                                        args=(diediedie, result_queue,))

    for p in procs + [processor]:
      p.start()

    # Import everything ending with _test and feed m.GenTests(self) into q.
    skip_dirs = ['support']
    for m, _ in import_helper(TEST_ROOT_PATH, 'tests', ['GenTests'], skip_dirs):
      for test in m.GenTests():
        test_queue.put(test)
        tests_run += 1

    test_queue.join()
    result_queue.join()
  finally:
    diediedie.set()

    for p in procs + [processor]:
      p.join()
      if p.exitcode:
        bad.append('%s did not exit cleanly.' % p.name)

    print 'Done (%s tests executed)' % tests_run
    print

  if bad:
    print 'Test did not complete successfully'
    print '\n'.join(bad)
    return

  c = coverage.coverage(data_file=coverage_base, data_suffix=True,
                        config_file=config_file)
  c.combine()
  print 'Coverage report:'
  c.report()


def import_helper(path, suffix, fromlist, skip_dirs=()):
  for base, dnames, fnames in os.walk(path):
    for skip in skip_dirs:
      if skip in dnames:
        dnames.remove(skip)
    for fname in fnames:
      if fname.endswith('_%s.py' % suffix):
        mod_base = 'tests_v2' + (
          base[len(TEST_ROOT_PATH):]
          .replace(os.path.sep, '.')
        )
        mod_name = '.'.join((mod_base, fname[:-3]))
        mod = __import__(mod_name, fromlist=fromlist)
        for x in fromlist:
          if not hasattr(mod, x):
            print 'Skipping %s because it does not have %s' % (mod_name, x)
            break
        else:
          yield mod, os.path.join(base, fname)

if __name__ == '__main__':
  run_all_tests()
