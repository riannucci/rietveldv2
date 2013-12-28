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

import os
import sys

import yaml

from tests_v2.main import TEST_ROOT_PATH, SRC_ROOT_PATH, TestFailed

EXPECT_ROOT_PATH = os.path.join(SRC_ROOT_PATH, 'test_expectations')

YAMLSafeLoader = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)
YAMLSafeDumper = getattr(yaml, 'CSafeDumper', yaml.SafeDumper)

UNSET = object()


def lazy_mkdir(d):
  try:
    os.makedirs(d)
  except OSError:
    pass


class Test(object):
  _current_expectation = UNSET

  def __init__(self, test_func, name, expect, args=None, kwargs=None):
    self._test_func = test_func
    self.args = args or ()
    self.kwargs = kwargs or {}
    self.expect = expect
    self.name = name


  @property
  def test_func(self):
    return self._test_func or getattr(self, '_execute')

  @property
  def current_expectation(self):
    if self._current_expectation is UNSET:
      try:
        with open(self.expect, 'r') as f:
          self._current_expectation = yaml.load(f, YAMLSafeLoader)
      except:
        self._current_expectation = None
    return self._current_expectation

  def train(self):
    ret = None
    current = self.current_expectation
    new = self.test_func(*self.args, **self.kwargs)
    if new != current:
      ret = 'Updated expectations for %r.' % self.name
      lazy_mkdir(os.path.dirname(self.expect))
      with open(self.expect, 'wb') as f:
        yaml.dump(new, f, YAMLSafeDumper, default_flow_style=False,
                  encoding='utf-8')
    return ret

  def run(self):
    current = self.current_expectation
    new = self.test_func(*self.args, **self.kwargs)
    if not new or not current or new != current:
      raise TestFailed(self.name, new, current)


def test_file_to_name_expectation(path, extra=''):
  path = os.path.abspath(path)
  assert path.startswith(TEST_ROOT_PATH+'/')
  name = os.path.splitext(path[len(TEST_ROOT_PATH)+1:])[0]
  if extra:
    name += extra
  expect = os.path.join(EXPECT_ROOT_PATH, name + '.yaml')
  return name, expect


def BasicTest(test_func, *args, **kwargs):
  name = sys.modules[test_func.__module__].__file__
  name, expect = test_file_to_name_expectation(name, '.' + test_func.__name__)
  return Test(test_func, name, expect, args, kwargs)
