import Queue
import coverage
import glob
import multiprocessing
import os
import shutil
import sys
import tempfile
import traceback
import difflib
import yaml

YAMLLoader = getattr(yaml, 'CLoader', yaml.Loader)
YAMLDumper = getattr(yaml, 'CDumper', yaml.Dumper)

SCRIPT = os.path.abspath(__file__)
TEST_ROOT_PATH = os.path.dirname(SCRIPT)
SRC_ROOT_PATH = os.path.dirname(os.path.dirname(SCRIPT))
EXPECT_ROOT_PATH = os.path.join(SRC_ROOT_PATH, 'test_expectations')

UNSET = object()


def lazy_mkdir(d):
  try:
    os.makedirs(d)
  except OSError:
    pass


class TestFailed(Exception):
  pass

class TestAbort(Exception):
  def __init__(self, name):
    super(TestAbort, self).__init__(name, traceback.format_exc())


class Test(object):
  _current_expectation = UNSET

  def __init__(self, test_func, expect_path, args=None,
               kwargs=None):
    self.test_func = test_func
    self.args = args or ()
    self.kwargs = kwargs or {}

    if expect_path.startswith(TEST_ROOT_PATH+'/'):
      expect_path = expect_path[len(TEST_ROOT_PATH)+1:]
    assert expect_path[0] != '/'
    self.name = os.path.splitext(expect_path)[0]
    expect_path += '.yaml'
    self.expect_path = os.path.join(EXPECT_ROOT_PATH, expect_path)

  @property
  def current_expectation(self):
    if self._current_expectation is UNSET:
      try:
        with open(self.expect_path, 'r') as f:
          self._current_expectation = yaml.load(f, YAMLLoader)
      except:
        self._current_expectation = None
    return self._current_expectation

  def train(self):
    ret = None
    current = self.current_expectation
    new = self.test_func(*self.args, **self.kwargs)
    if new != current:
      ret = 'Updated expectations for %r.' % self.name
      lazy_mkdir(os.path.dirname(self.expect_path))
      with open(self.expect_path, 'w') as f:
        yaml.dump(new, f, YAMLDumper, default_flow_style=False)
    return ret

  def run(self):
    current = self.current_expectation
    new = self.test_func(*self.args, **self.kwargs)
    if not new or not current or new != current:
      raise TestFailed(self.name, new, current)


POLL_IVAL = 0.1


def TestRunner(diediedie, training, test_queue, result_queue):
  cov = coverage.coverage(config_file=os.environ['COVERAGE_PROCESS_START'])
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
        rslt = e
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
    print '%s: %s' % (name, data or 'OK')
    result_queue.task_done()


TMP_SUFFIX = '.rietveld_tests'

def run_all_tests():
  for f in glob.glob(os.path.join(tempfile.gettempdir(), '*%s' % TMP_SUFFIX)):
    print 'removing left-over temp dir %r' % f
    shutil.rmtree(f, ignore_errors=True)

  tmp_dir = tempfile.mkdtemp(TMP_SUFFIX)
  coverage_base = os.path.join(tmp_dir, '.coverage')
  config_file = os.path.join(tmp_dir, 'coverage.cfg')
  usercustomize_file = os.path.join(tmp_dir, 'lib', 'python', 'site-packages',
                                    'usercustomize.py')

  train = '--train' in sys.argv

  bad = []
  try:
    with open(config_file, 'w') as f:
      print >> f, '[run]'
      print >> f, 'parallel = True'
      print >> f, 'data_file = %s' % coverage_base
      print >> f, 'include ='
      print >> f, '  %s/*' % SRC_ROOT_PATH
      print >> f, 'omit ='
      print >> f, '  %s/codereview' % SRC_ROOT_PATH
      print >> f, '  %s/static' % SRC_ROOT_PATH
      print >> f, '  %s/templates' % SRC_ROOT_PATH
      print >> f, '  %s/tools' % SRC_ROOT_PATH
      print >> f, '  %s/mapreduce' % SRC_ROOT_PATH
      print >> f, '  %s/test*' % SRC_ROOT_PATH
      print >> f, '  %s/*/__init__.py' % SRC_ROOT_PATH

    os.makedirs(os.path.dirname(usercustomize_file))
    with open(usercustomize_file, 'w') as f:
      print >> f, 'import coverage'
      print >> f, 'coverage.process_startup()'

    os.environ['PYTHONUSERBASE'] = tmp_dir
    os.environ['COVERAGE_PROCESS_START'] = config_file

    os.environ['DJANGO_SETTINGS_MODULE'] = 'settings'
    os.environ['SERVER_SOFTWARE'] = 'Dev-Testing'

    sys.path.insert(0, SRC_ROOT_PATH)
  except Exception as e:
    print 'Failed to set up environment!', e
    return

  tests_run = 0
  try:
    diediedie = multiprocessing.Event()
    test_queue = multiprocessing.JoinableQueue()
    result_queue = multiprocessing.JoinableQueue()
    procs = [multiprocessing.Process(
        target=TestRunner, args=(diediedie, train, test_queue, result_queue))]
    processor = multiprocessing.Process(target=TestResultProcessor,
                                        args=(diediedie, result_queue,))

    for p in procs + [processor]:
      p.start()

    # Import everything ending with _test and feed m.GenTests(self) into q.
    for base, _, fnames in os.walk(TEST_ROOT_PATH):
      mod_base = 'tests_v2.' + base[len(TEST_ROOT_PATH):].replace('/', '.')
      for fname in fnames:
        if fname.endswith('_test.py'):
          mod_name = mod_base + fname[:-3]
          mod = __import__(mod_name, fromlist=['GenTests'])
          if not hasattr(mod, 'GenTests'):
            print 'Skipping %s because it does not have GenTests' % mod_name
            continue
          for test in mod.GenTests(Test):
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

  shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == '__main__':
  run_all_tests()
