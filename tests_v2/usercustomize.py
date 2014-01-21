# vim: ft=python:

# Hack to allow coverage to save data even inside devappserver2's
# sandbox.  Cool dynamic injection into the module closure, bro.
import coverage.data
real_open = open
coverage.data.open = real_open

# devappserver indiscriminantly uses an uncatchable SIGKILL instead of
# a SIGTERM, so politely fix that for them.
import subprocess

import signal
SIGTERM = signal.SIGTERM
subprocess.Popen.kill = (
  lambda self: self.send_signal(SIGTERM))

# devappserver also stubs a couple of these methods which makes
# coverage's data_suffix be fairly deterministic, which makes the coverage
# names collide.
import os
import random
import socket
data_suffix = "%s.%s.%06d" % (
  socket.gethostname(), os.getpid(),
  random.randint(0, 999999))

# Fire up coverage
import coverage

cov = coverage.coverage(config_file=os.environ['COVERAGE_PROCESS_START'],
                        data_suffix=data_suffix)
cov.start()
def stop_coverage():
  if cov._started:  # pylint: disable=W0212
    cov.stop()
    cov.save()

# Now for the monkeypatch. We want to install our own handlers for
# TERM and INT, but ALSO wrap any handlers which devappserver will
# install.
SIG_IGN = signal.SIG_IGN
SIGINT = signal.SIGINT
def shtap_wrapper(f):
  def wrapped(*args, **kwargs):
    stop_coverage()
    if callable(f):
      f(*args, **kwargs)
    elif f == SIG_IGN:
      pass
    else:  # assume it's really a signal handler
      if args[0] == SIGINT:
        raise KeyboardInterrupt()
      else:
        os._exit(1)  # pylint: disable=W0212
  return wrapped
def new_signal(signum, handler, _orig=signal.signal):
  return _orig(signum, shtap_wrapper(handler))
signal.signal = new_signal
signal.signal(signal.SIGTERM, signal.SIG_DFL)
signal.signal(signal.SIGINT, signal.SIG_DFL)

# And do it the normal way for good measure.
import atexit
atexit.register(stop_coverage)

# Once more, with feeling
def new_exit(code, _old_exit=os._exit):  # pylint: disable=W0212
  stop_coverage()
  _old_exit(code)
os._exit = new_exit  # pylint: disable=W0212
