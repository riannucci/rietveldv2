  @ndb.tasklet
  def lines_async(self):
    data = yield self.data_async
    raise ndb.Return(utils.LazyLineSplitter(data, self.lineending))
  def _seq_matcher(self):
    return difflib.SequenceMatcher(None, self.old.lines_async.get_result(),
                                   self.new.lines_async.get_result())

      seq_matcher = self._seq_matcher()
          for line in seq_matcher.a[i1:i2]:
          for line in seq_matcher.a[i1:i2]:
          for line in seq_matcher.b[j1:j2]:
    seq_matcher = self._seq_matcher()