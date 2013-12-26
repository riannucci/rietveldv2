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

  def __init__(self, test_func, expect_path, args=None,
               kwargs=None):
    self._test_func = test_func
    self.args = args or ()
    self.kwargs = kwargs or {}

    if expect_path.startswith(TEST_ROOT_PATH+'/'):
      expect_path = expect_path[len(TEST_ROOT_PATH)+1:]
    assert expect_path[0] != '/'
    self.name = os.path.splitext(expect_path)[0]
    expect_path += '.yaml'
    self.expect_path = os.path.join(EXPECT_ROOT_PATH, expect_path)

  @property
  def test_func(self):
    return self._test_func or getattr(self, '_execute')

  @property
  def current_expectation(self):
    if self._current_expectation is UNSET:
      try:
        with open(self.expect_path, 'r') as f:
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
      lazy_mkdir(os.path.dirname(self.expect_path))
      with open(self.expect_path, 'wb') as f:
        yaml.dump(new, f, YAMLSafeDumper, default_flow_style=False,
                  encoding='utf-8')
    return ret

  def run(self):
    current = self.current_expectation
    new = self.test_func(*self.args, **self.kwargs)
    if not new or not current or new != current:
      raise TestFailed(self.name, new, current)
