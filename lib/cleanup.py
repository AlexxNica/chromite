# Copyright (c) 2012 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import signal
import sys

from chromite.lib import cros_build_lib
from chromite.lib import locking


class EnforcedCleanupSection(object):

  """Context manager used to ensure that a section of cleanup code is run

  This is designed such that a child splits off, ensuring that even if the
  parent is sigkilled, the section marked *will* be run.  This is implemented
  via a ProcessLock shared between parent, and a process split off to
  survive any sigkills/hard crashes in the parent.

  The usage of this is basically in a pseudo-transactional manner:

  >>> with EnforcedCleanupSection() as critical:
  ...   with other_handler:
  ...     try:
  ...       critical.ForkWatchdog()
  ...       # Everything past here doesn't run during enforced cleanup
  ...       # ... normal code
  ...      finally:
  ...        pass # This is guaranteed to run.
  ...    # The __exit__ for other_handler is guaranteed to run.
  ...  # Anything from this point forward will only be run by the invoking
  ...  # process. If cleanup enforcement had to occur, any code from this
  ...  # point forward won't be run.
  >>>
  """
  def __init__(self):
    self._lock = locking.ProcessLock(verbose=False)
    self._forked = False
    self._is_child = False

  def ForkWatchdog(self):
    if self._forked:
      raise RuntimeError("MarkCleanup was invoked twice for %s" % (self,))
    self._lock.write_lock()

    pid = os.fork()
    self._forked = True

    if pid:
      # Parent; nothing further to do here.
      return

    # Get ourselves a new process group; note that we do not reparent
    # to init.
    os.setsid()

    # Since we share stdin/stdout/whatever, suppress sigint should we somehow
    # become the foreground process in the session group.
    # pylint: disable=W0212
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    # Child code.  We lose the lock via lockf/fork semantics.
    self._is_child = True
    try:
      self._lock.write_lock()
    except Exception, e:
      print >> sys.stderr, ("EnforcedCleanupSection %s excepted(%r) attempting "
                            "to take the write lock; hard exiting." % (self, e))
      # We have no way of knowing the state of the parent if this locking
      # fails- failure means a code bug.  Specifically, we don't know if
      # cleanup code was run, thus just flat out bail.
      os._exit(1)
    # Check if the parent exited cleanly; if so, we don't need to do anything.

    if os.read(self._lock.fd, 1):
      for handle in (sys.__stdin__, sys.__stdout__, sys.__stderr__):
        try:
          handle.flush()
        except EnvironmentError:
          pass
      os._exit(0)

    # Allow masterpid context managers to run in this case, since we're
    # explicitly designed for this cleanup.
    cros_build_lib.MasterPidContextManager._ALTERNATE_MASTER_PID = os.getpid()

    raise RuntimeError("Parent exited uncleanly; forcing cleanup code to run.")

  def __enter__(self):
    self._lock.write_lock()
    return self

  def __exit__(self, exc, exc_type, tb):
    if self._is_child:
      # All cleanup code that would've run, has ran.
      # Hard exit to bypass any further code execution.
      # pylint: disable=W0212
      os._exit(0)
    os.write(self._lock.fd, '\n')
    self._lock.unlock()
