
import sys
import logging

from google.appengine.ext.ndb import tasklets, eventloop

orig_set_result = tasklets.Future.set_result
def set_result(self, result):
  try:
    orig_set_result(self, result)
  except Exception, err:
    _, _, tb = sys.exc_info()
    # pylint: disable=W0212
    self._result = None
    self._exception = err
    self._traceback = tb

    # Since the orig_set_result never made it far enough, we need to call
    # the delayed callbacks.
    for callback, args, kwds in self._callbacks:
      eventloop.queue_call(None, callback, *args, **kwds)
    logging.warn("%s threw in immediate_callback.", self)
tasklets.Future.set_result = set_result
