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

import difflib
import types
import collections
import hashlib

from google.appengine.ext import ndb

from framework import utils


class LazyLineSplitter(collections.Sequence):
  def __init__(self, data, lineending):
    self._data = data
    self._offsets = []

    start = 0
    end = None
    while start < len(data):
      end = data.find(lineending, start)
      if end == -1:
        self._offsets.append((start, len(data)))
        break
      self._offsets.append(start, end)
      start = end + len(lineending)

  def __getitem__(self, idx):
    start, stop = self._offsets[idx]
    return self._data[start:stop]

  def __len__(self):
    return len(self._offsets)


class Diffable(object):
  def __init__(self, path, timestamp, mode, lineending):
    self.path = path
    self.timestamp = timestamp
    self.mode = int(mode)

    # TODO(iannucci): Support binary Diffable objects
    assert lineending is not None
    self.lineending = lineending

  @utils.cached_property
  def data_async(self):
    raise NotImplementedError()

  @utils.cached_property
  def lines(self):
    return LazyLineSplitter(self.data_async.get_result(), self.lineending)

  @utils.cached_property
  def size(self):
    raise NotImplementedError()

  @utils.cached_property
  @ndb.tasklet
  def git_csum_async(self):
    r = hashlib.sha1('blob %d\0' % self.size)
    r.update((yield self.data_async))
    raise ndb.Return(r.hexdigest())

  def to_dict(self):
    return {
      'path': self.path,
      'timestamp': self.timestamp,
      'mode': self.mode,
    }


class DiffablePair(object):
  def __init__(self, old, new, action):
    assert isinstance(old, (Diffable, types.NoneType))
    assert isinstance(new, (Diffable, types.NoneType))

    if action is None:
      if old and new:
        action = 'modify'
      elif old:
        action = 'delete'
      elif new:
        action = 'add'
    assert action in ('modify', 'add', 'copy', 'delete', 'rename')

    if action == 'modify':
      assert old.path == new.path
    elif action in ('copy', 'rename'):
      assert old.path != new.path
    elif action == 'add':
      assert new and not old
    elif action == 'delete':
      assert old and not new
    else:
      assert False, 'wat'
    self.old = old
    self.new = new
    self.action = action

  def to_dict(self):
    return {
      'old': self.old.to_dict(),
      'new': self.new.to_dict(),
      'action': self.action,
    }

  def _diff_body(self, n, nl, seq_matcher=None):
    if seq_matcher is None:
      seq_matcher = difflib.SequenceMatcher(None, self.old.lines,
                                            self.new.lines)
    old, new = self.old, self.new
    started = False
    for group in seq_matcher.get_grouped_opcodes(n):
      if not started:
        yield '--- a/%s %s%s' % (old.path, old.timestamp, nl)
        yield '+++ b/%s %s%s' % (new.path, new.timestamp, nl)
        started = True
      i1, i2, j1, j2 = group[0][1], group[-1][2], group[0][3], group[-1][4]
      yield "@@ -%d,%d +%d,%d @@%s" % (i1+1, i2-i1, j1+1, j2-j1, nl)
      for tag, i1, i2, j1, j2 in group:
        if tag == 'equal':
          for line in old.data[i1:i2]:
            yield ' ' + line
          continue
        if tag == 'replace' or tag == 'delete':
          for line in old.data[i1:i2]:
            yield '-' + line
        if tag == 'replace' or tag == 'insert':
          for line in new.data[j1:j2]:
            yield '+' + line

  def generate_diff(self, mode, **kwargs):
    differ = {
      'git': self.generate_git_diff,
      'svn': self.generate_svn_diff,
    }.get(mode, None)
    assert differ is not None
    return differ(**kwargs)

  def generate_svn_diff(self, n=3, nl='\n'):
    if self.action in ('copy', 'rename'):
      raise NotImplementedError('SVN patches with move/rename actions are not'
                                ' supported.')

    old, new = self.old, self.new
    assert old.path == new.path
    path = old.path
    yield 'Index: %s%s' % (path, nl)
    yield '%s%s' % ('=' * 67, nl)

    for line in self._diff_body(n, nl):
      yield line

    if old.mode != new.mode:
      yield 'Property changes on: %s%s' % (path, nl)
      yield '%s%s' % ('_' * 67, nl)
      if new.mode & 0100:  # exe bit is set
        yield 'Added: svn:executable%s' % nl
        yield '## -0,0 +1 ##%s' % nl
        yield '+*%s' % nl
      else:
        yield 'Deleted: svn:executable%s' % nl
        yield '## -1 +0,0 ##%s' % nl
        yield '-*%s' % nl
      yield r'\ No newline at end of property%s' % nl

  def generate_git_diff(self, n=3, nl='\n'):
    # TODO(iannucci): Produce hunk summaries, i.e.
    # @@ ... @@ int main()
    seq_matcher = difflib.SequenceMatcher(None, self.old.lines, self.new.lines)
    old, new = self.old, self.new

    if self.action == 'modify':
      yield 'diff --git a/%s b/%s' % (old.path, new.path)
      if old.mode == new.mode:
        yield 'old mode %06o%s' % (old.mode, nl)
        yield 'new mode %06o%s' % (new.mode, nl)
        yield 'index %s..%s%s' % (old.csum, new.csum, nl)
      else:
        yield 'index %s..%s %s%s' % (old.csum, new.csum, old.mode, nl)

    elif self.action == 'add':
      yield 'diff --git a/%s b/%s' % (new.path, new.path)
      yield 'new file mode %06o%s' % (new.mode, nl)
      yield 'index %s..%s%s' % ('0'*40, new.csum, nl)

    elif self.action == 'delete':
      yield 'diff --git a/%s b/%s' % (old.path, old.path)
      yield 'deleted file mode %06o%s' % (old.mode, nl)
      yield 'index %s..%s%s' % (old.csum, '0'*40, nl)

    elif self.action in ('copy', 'rename'):
      yield 'diff --git a/%s b/%s' % (old.path, new.path)
      if old.mode == new.mode:
        yield 'old mode %06o%s' % (old.mode, nl)
        yield 'new mode %06o%s' % (new.mode, nl)
      similarity = seq_matcher.ratio()
      yield 'similarity index %d%%' % (similarity * 100)
      yield '%s from %s%s' % (self.action, old.path, nl)
      yield '%s to %s%s' % (self.action, new.path, nl)
      if similarity < 1.0:
        yield 'index %s..%s%s' % (old.csum, new.csum, nl)
      else:
        return

    for line in self._diff_body(n, nl, seq_matcher):
      yield line

    # TODO(iannucci): Worry about missing newline at end of files?
