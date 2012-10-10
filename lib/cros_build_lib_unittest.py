#!/usr/bin/python
#
# Copyright (c) 2012 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import contextlib
import errno
import itertools
import logging
import mox
import signal
import time
import __builtin__

from chromite.buildbot import constants
from chromite.lib import cros_build_lib
from chromite.lib import cros_test_lib
from chromite.lib import osutils
from chromite.lib import signals as cros_signals

# pylint: disable=W0212,R0904

class TestRunCommandNoMock(cros_test_lib.TestCase):
  """Class that tests RunCommand by not mocking subprocess.Popen"""

  def testErrorCodeNotRaisesError(self):
    """Don't raise exception when command returns non-zero exit code."""
    result = cros_build_lib.RunCommand(['ls', '/does/not/exist'],
                                        error_code_ok=True)
    self.assertTrue(result.returncode != 0)

  def testReturnCodeNotZeroErrorOkNotRaisesError(self):
    """Raise error when proc.communicate() returns non-zero."""
    self.assertRaises(cros_build_lib.RunCommandError, cros_build_lib.RunCommand,
                      ['/does/not/exist'])


def _ForceLoggingLevel(functor):
  def inner(*args, **kwds):
    current = cros_build_lib.logger.getEffectiveLevel()
    try:
      cros_build_lib.logger.setLevel(logging.INFO)
      return functor(*args, **kwds)
    finally:
      cros_build_lib.logger.setLevel(current)
  return inner


class TestRunCommand(cros_test_lib.MoxTestCase):

  def setUp(self):
    # Get the original value for SIGINT so our signal() mock can return the
    # correct thing.
    self._old_sigint = signal.getsignal(signal.SIGINT)

    self.mox.StubOutWithMock(cros_build_lib, '_Popen', use_mock_anything=True)
    self.mox.StubOutWithMock(signal, 'signal')
    self.mox.StubOutWithMock(signal, 'getsignal')
    self.mox.StubOutWithMock(cros_signals, 'SignalModuleUsable')
    self.proc_mock = self.mox.CreateMockAnything()
    self.error = 'test error'
    self.output = 'test output'

  @contextlib.contextmanager
  def _SetupPopen(self, cmd, **kwds):
    cros_signals.SignalModuleUsable().AndReturn(True)
    ignore_sigint = kwds.pop('ignore_sigint', False)

    for val in ('cwd', 'stdin', 'stdout', 'stderr'):
      kwds.setdefault(val, None)
    kwds.setdefault('shell', False)
    kwds.setdefault('env', mox.IgnoreArg())
    kwds['close_fds'] = True

    # Make some arbitrary functors we can pretend are signal handlers.
    # Note that these are intentionally defined on the fly via lambda-
    # this is to ensure that they're unique to each run.
    sigint_suppress = lambda signum, frame:None
    sigint_suppress.__name__ = 'sig_ign_sigint'
    normal_sigint = lambda signum, frame:None
    normal_sigint.__name__ = 'sigint'
    normal_sigterm = lambda signum, frame:None
    normal_sigterm.__name__ = 'sigterm'

    # If requested, RunCommand will ignore sigints; record that.
    if ignore_sigint:
      signal.signal(signal.SIGINT, signal.SIG_IGN).AndReturn(sigint_suppress)
    else:
      signal.getsignal(signal.SIGINT).AndReturn(normal_sigint)
      signal.signal(signal.SIGINT, mox.IgnoreArg()).AndReturn(normal_sigint)
    signal.getsignal(signal.SIGTERM).AndReturn(normal_sigterm)
    signal.signal(signal.SIGTERM, mox.IgnoreArg()).AndReturn(normal_sigterm)

    cros_build_lib._Popen(cmd, **kwds).AndReturn(self.proc_mock)
    yield self.proc_mock

    # If it ignored them, RunCommand will restore sigints; record that.
    if ignore_sigint:
      signal.signal(signal.SIGINT, sigint_suppress).AndReturn(signal.SIG_IGN)
    else:
      signal.signal(signal.SIGINT, normal_sigint).AndReturn(None)
    signal.signal(signal.SIGTERM, normal_sigterm).AndReturn(None)

  def _AssertCrEqual(self, expected, actual):
    """Helper method to compare two CommandResult objects.

    This is needed since assertEqual does not know how to compare two
    CommandResult objects.

    Args:
      expected: a CommandResult object, expected result.
      actual: a CommandResult object, actual result.
    """
    self.assertEqual(expected.cmd, actual.cmd)
    self.assertEqual(expected.error, actual.error)
    self.assertEqual(expected.output, actual.output)
    self.assertEqual(expected.returncode, actual.returncode)

  @_ForceLoggingLevel
  def _TestCmd(self, cmd, real_cmd, sp_kv=dict(), rc_kv=dict(), sudo=False):
    """Factor out common setup logic for testing RunCommand().

    Args:
      cmd: a string or an array of strings that will be passed to RunCommand.
      real_cmd: the real command we expect RunCommand to call (might be
          modified to have enter_chroot).
      sp_kv: key-value pairs passed to subprocess.Popen().
      rc_kv: key-value pairs passed to RunCommand().
      sudo: use SudoRunCommand() rather than RunCommand().
    """
    expected_result = cros_build_lib.CommandResult()
    expected_result.cmd = real_cmd
    expected_result.error = self.error
    expected_result.output = self.output
    expected_result.returncode = self.proc_mock.returncode

    arg_dict = dict()
    for attr in 'close_fds cwd env stdin stdout stderr shell'.split():
      if attr in sp_kv:
        arg_dict[attr] = sp_kv[attr]
      else:
        if attr == 'close_fds':
          arg_dict[attr] = True
        elif attr == 'shell':
          arg_dict[attr] = False
        else:
          arg_dict[attr] = None

    with self._SetupPopen(real_cmd,
                          ignore_sigint=rc_kv.get('ignore_sigint'),
                          **sp_kv) as proc:
      proc.communicate(None).AndReturn((self.output, self.error))

    self.mox.ReplayAll()
    if sudo:
      actual_result = cros_build_lib.SudoRunCommand(cmd, **rc_kv)
    else:
      actual_result = cros_build_lib.RunCommand(cmd, **rc_kv)
    self.mox.VerifyAll()

    self._AssertCrEqual(expected_result, actual_result)

  def testReturnCodeZeroWithArrayCmd(self, ignore_sigint=False):
    """--enter_chroot=False and --cmd is an array of strings.

    Parameterized so this can also be used by some other tests w/ alternate
    params to RunCommand().

    Args:
      ignore_sigint: If True, we'll tell RunCommand to ignore sigint.
    """
    self.proc_mock.returncode = 0
    cmd_list = ['foo', 'bar', 'roger']
    self._TestCmd(cmd_list, cmd_list,
                  rc_kv=dict(ignore_sigint=ignore_sigint))

  def testSignalRestoreNormalCase(self):
    """Test RunCommand() properly sets/restores sigint.  Normal case."""
    self.testReturnCodeZeroWithArrayCmd(ignore_sigint=True)

  def testReturnCodeZeroWithArrayCmdEnterChroot(self):
    """--enter_chroot=True and --cmd is an array of strings."""
    self.proc_mock.returncode = 0
    cmd_list = ['foo', 'bar', 'roger']
    real_cmd = ['cros_sdk', '--'] + cmd_list
    self._TestCmd(cmd_list, real_cmd, rc_kv=dict(enter_chroot=True))

  @_ForceLoggingLevel
  def testCommandFailureRaisesError(self, ignore_sigint=False):
    """Verify error raised by communicate() is caught.

    Parameterized so this can also be used by some other tests w/ alternate
    params to RunCommand().

    Args:
      ignore_sigint: If True, we'll tell RunCommand to ignore sigint.
    """
    cmd = 'test cmd'


    with self._SetupPopen(['/bin/bash', '-c', cmd],
                          ignore_sigint=ignore_sigint) as proc:
      proc.communicate(None).AndReturn((self.output, self.error))

    self.mox.ReplayAll()
    self.assertRaises(cros_build_lib.RunCommandError,
                      cros_build_lib.RunCommand, cmd, shell=True,
                      ignore_sigint=ignore_sigint, error_code_ok=False)
    self.mox.VerifyAll()

  @_ForceLoggingLevel
  def testSubprocessCommunicateExceptionRaisesError(self, ignore_sigint=False):
    """Verify error raised by communicate() is caught.

    Parameterized so this can also be used by some other tests w/ alternate
    params to RunCommand().

    Args:
      ignore_sigint: If True, we'll tell RunCommand to ignore sigint.
    """
    cmd = ['test', 'cmd']

    with self._SetupPopen(cmd, ignore_sigint=ignore_sigint) as proc:
      proc.communicate(None).AndRaise(ValueError)

    self.mox.ReplayAll()
    self.assertRaises(ValueError, cros_build_lib.RunCommand, cmd,
                      ignore_sigint=ignore_sigint)
    self.mox.VerifyAll()

  def testSignalRestoreExceptionCase(self):
    """Test RunCommand() properly sets/restores sigint.  Exception case."""
    self.testSubprocessCommunicateExceptionRaisesError(ignore_sigint=True)

  @_ForceLoggingLevel
  def testSubprocessCommunicateExceptionNotRaisesError(self):
    """Don't re-raise error from communicate() when --error_ok=True."""
    cmd = ['test', 'cmd']
    real_cmd = ['cros_sdk', '--'] + cmd
    expected_result = cros_build_lib.CommandResult()
    expected_result.cmd = real_cmd

    with self._SetupPopen(real_cmd) as proc:
      proc.communicate(None).AndRaise(ValueError)

    self.mox.ReplayAll()
    actual_result = cros_build_lib.RunCommand(cmd, error_ok=True,
                                              enter_chroot=True)
    self.mox.VerifyAll()

    self._AssertCrEqual(expected_result, actual_result)

  def testEnvWorks(self):
    """Test RunCommand(..., env=xyz) works."""
    # We'll put this bogus environment together, just to make sure
    # subprocess.Popen gets passed it.
    env = {'Tom': 'Jerry', 'Itchy': 'Scratchy'}

    # This is a simple case, copied from testReturnCodeZeroWithArrayCmd()
    self.proc_mock.returncode = 0
    cmd_list = ['foo', 'bar', 'roger']

    # Run.  We expect the env= to be passed through from sp (subprocess.Popen)
    # to rc (RunCommand).
    self._TestCmd(cmd_list, cmd_list,
                  sp_kv=dict(env=env),
                  rc_kv=dict(env=env))

  def testExtraEnvOnlyWorks(self):
    """Test RunCommand(..., extra_env=xyz) works."""
    # We'll put this bogus environment together, just to make sure
    # subprocess.Popen gets passed it.
    extra_env = {'Pinky' : 'Brain'}
    ## This is a little bit circular, since the same logic is used to compute
    ## the value inside, but at least it checks that this happens.
    total_env = os.environ.copy()
    total_env.update(extra_env)

    # This is a simple case, copied from testReturnCodeZeroWithArrayCmd()
    self.proc_mock.returncode = 0
    cmd_list = ['foo', 'bar', 'roger']

    # Run.  We expect the env= to be passed through from sp (subprocess.Popen)
    # to rc (RunCommand).
    self._TestCmd(cmd_list, cmd_list,
                  sp_kv=dict(env=total_env),
                  rc_kv=dict(extra_env=extra_env))

  def testExtraEnvTooWorks(self):
    """Test RunCommand(..., env=xy, extra_env=z) works."""
    # We'll put this bogus environment together, just to make sure
    # subprocess.Popen gets passed it.
    env = {'Tom': 'Jerry', 'Itchy': 'Scratchy'}
    extra_env = {'Pinky': 'Brain'}
    total_env = {'Tom': 'Jerry', 'Itchy': 'Scratchy', 'Pinky': 'Brain'}

    # This is a simple case, copied from testReturnCodeZeroWithArrayCmd()
    self.proc_mock.returncode = 0
    cmd_list = ['foo', 'bar', 'roger']

    # Run.  We expect the env= to be passed through from sp (subprocess.Popen)
    # to rc (RunCommand).
    self._TestCmd(cmd_list, cmd_list,
                  sp_kv=dict(env=total_env),
                  rc_kv=dict(env=env, extra_env=extra_env))

  def testExceptionEquality(self):
    """Verify equality methods for RunCommandError"""

    c1 = cros_build_lib.CommandResult(cmd=['ls', 'arg'], returncode=1)
    c2 = cros_build_lib.CommandResult(cmd=['ls', 'arg1'], returncode=1)
    c3 = cros_build_lib.CommandResult(cmd=['ls', 'arg'], returncode=2)
    e1 = cros_build_lib.RunCommandError('Message 1', c1)
    e2 = cros_build_lib.RunCommandError('Message 1', c1)
    e_diff_msg = cros_build_lib.RunCommandError('Message 2', c1)
    e_diff_cmd = cros_build_lib.RunCommandError('Message 1', c2)
    e_diff_code = cros_build_lib.RunCommandError('Message 1', c3)

    self.assertEqual(e1, e2)
    self.assertNotEqual(e1, e_diff_msg)
    self.assertNotEqual(e1, e_diff_cmd)
    self.assertNotEqual(e1, e_diff_code)

  def testSudoRunCommand(self):
    """Test SudoRunCommand(...) works."""
    cmd_list = ['foo', 'bar', 'roger']
    sudo_list = ['sudo', '--'] + cmd_list
    self.proc_mock.returncode = 0
    self._TestCmd(cmd_list, sudo_list, sudo=True)

  def testSudoRunCommandShell(self):
    """Test SudoRunCommand(..., shell=True) works."""
    cmd = 'foo bar roger'
    sudo_list = ['sudo', '--', '/bin/bash', '-c', cmd]
    self.proc_mock.returncode = 0
    self._TestCmd(cmd, sudo_list, sudo=True,
                  rc_kv=dict(shell=True))

  def testSudoRunCommandEnv(self):
    """Test SudoRunCommand(..., extra_env=z) works."""
    cmd_list = ['foo', 'bar', 'roger']
    sudo_list = ['sudo', 'shucky=ducky', '--'] + cmd_list
    extra_env = {'shucky' : 'ducky'}
    self.proc_mock.returncode = 0
    self._TestCmd(cmd_list, sudo_list, sudo=True,
                  rc_kv=dict(extra_env=extra_env))

  def testSudoRunCommandUser(self):
    """Test SudoRunCommand(..., user='...') works."""
    cmd_list = ['foo', 'bar', 'roger']
    sudo_list = ['sudo', '-u', 'MMMMMonster', '--'] + cmd_list
    self.proc_mock.returncode = 0
    self._TestCmd(cmd_list, sudo_list, sudo=True,
                  rc_kv=dict(user='MMMMMonster'))

  def testSudoRunCommandUserShell(self):
    """Test SudoRunCommand(..., user='...', shell=True) works."""
    cmd = 'foo bar roger'
    sudo_list = ['sudo', '-u', 'MMMMMonster', '--', '/bin/bash', '-c', cmd]
    self.proc_mock.returncode = 0
    self._TestCmd(cmd, sudo_list, sudo=True,
                  rc_kv=dict(user='MMMMMonster', shell=True))


class TestRunCommandLogging(cros_test_lib.TempDirTestCase):

  @_ForceLoggingLevel
  def testLogStdoutToFile(self):
    # Make mox happy.
    log = os.path.join(self.tempdir, 'output')
    ret = cros_build_lib.RunCommand(
        ['python', '-c', 'print "monkeys"'], log_stdout_to_file=log)
    self.assertEqual(osutils.ReadFile(log), 'monkeys\n')
    self.assertTrue(ret.output is None)
    self.assertTrue(ret.error is None)

    # Validate dumb api usage.
    ret = cros_build_lib.RunCommand(
        ['python', '-c', 'import sys;print "monkeys2"'],
        log_stdout_to_file=log, redirect_stdout=True)
    self.assertTrue(ret.output is None)
    self.assertEqual(osutils.ReadFile(log), 'monkeys2\n')

    os.unlink(log)
    ret = cros_build_lib.RunCommand(
        ['python', '-c', 'import sys;print >> sys.stderr, "monkeys3"'],
        log_stdout_to_file=log, redirect_stderr=True)
    self.assertEqual(ret.error, 'monkeys3\n')
    self.assertTrue(os.path.exists(log))
    self.assertEqual(os.stat(log).st_size, 0)

    os.unlink(log)
    ret = cros_build_lib.RunCommand(
        ['python', '-u', '-c',
         'import sys;print "monkeys4"\nprint >> sys.stderr, "monkeys5"\n'],
        log_stdout_to_file=log, combine_stdout_stderr=True)
    self.assertTrue(ret.output is None)
    self.assertTrue(ret.error is None)

    self.assertEqual(osutils.ReadFile(log), 'monkeys4\nmonkeys5\n')


class TestRunCommandWithRetries(cros_test_lib.TestCase):

  @osutils.TempDirDecorator
  def testBasicRetry(self):
    # pylint: disable=E1101
    path = os.path.join(self.tempdir, 'script')
    paths = {
      'stop': os.path.join(self.tempdir, 'stop'),
      'store': os.path.join(self.tempdir, 'store')
    }
    osutils.WriteFile(path,
        "import sys\n"
        "val = int(open(%(store)r).read())\n"
        "stop_val = int(open(%(stop)r).read())\n"
        "open(%(store)r, 'w').write(str(val + 1))\n"
        "print val\n"
        "sys.exit(0 if val == stop_val else 1)\n" % paths)

    os.chmod(path, 0755)

    def _setup_counters(start, stop, sleep, sleep_cnt):
      self.mox.ResetAll()
      for i in xrange(sleep_cnt):
        time.sleep(sleep * (i + 1))
      self.mox.ReplayAll()

      osutils.WriteFile(paths['store'], str(start))
      osutils.WriteFile(paths['stop'], str(stop))

    self.mox = mox.Mox()
    self.mox.StubOutWithMock(time, 'sleep')
    self.mox.ReplayAll()

    _setup_counters(0, 0, 0, 0)
    command = ['python', path]
    kwds = {'redirect_stdout': True, 'print_cmd': False}

    self.assertEqual(cros_build_lib.RunCommand(command, **kwds).output, '0\n')

    func = cros_build_lib.RunCommandWithRetries

    _setup_counters(2, 2, 0, 0)
    self.assertEqual(func(0, command, sleep=0, **kwds).output, '2\n')
    self.mox.VerifyAll()

    _setup_counters(0, 2, 1, 2)
    self.assertEqual(func(2, command, sleep=1, **kwds).output, '2\n')
    self.mox.VerifyAll()

    _setup_counters(0, 1, 2, 1)
    self.assertEqual(func(1, command, sleep=2, **kwds).output, '1\n')
    self.mox.VerifyAll()

    _setup_counters(0, 3, 3, 2)
    self.assertRaises(cros_build_lib.RunCommandError,
                      func, 2, command, sleep=3, **kwds)
    self.mox.VerifyAll()


class TestTimedCommand(cros_test_lib.MoxTestCase):
  """Tests for TimedCommand()"""

  def setUp(self):
    self.mox.StubOutWithMock(cros_build_lib, 'RunCommand')

  def testBasic(self):
    """Make sure simple stuff works."""
    cros_build_lib.RunCommand(['true', 'foo'])
    self.mox.ReplayAll()

    cros_build_lib.TimedCommand(cros_build_lib.RunCommand, ['true', 'foo'])

  def testArgs(self):
    """Verify passing of optional args to the destination function."""
    cros_build_lib.RunCommand(':', shell=True, print_cmd=False,
                              error_code_ok=False)
    self.mox.ReplayAll()

    cros_build_lib.TimedCommand(cros_build_lib.RunCommand, ':', shell=True,
                                print_cmd=False, error_code_ok=False)

  def testLog(self):
    """Verify logging does the right thing."""
    self.mox.StubOutWithMock(cros_build_lib.logger, 'log')

    cros_build_lib.RunCommand('fun', shell=True)
    cros_build_lib.logger.log(mox.IgnoreArg(), 'msg! %s', mox.IgnoreArg())
    self.mox.ReplayAll()

    cros_build_lib.TimedCommand(cros_build_lib.RunCommand, 'fun',
                                timed_log_msg='msg! %s', shell=True)


class TestListFiles(cros_test_lib.TempDirTestCase):

  def _CreateNestedDir(self, dir_structure):
    for entry in dir_structure:
      full_path = os.path.join(os.path.join(self.tempdir, entry))
      # ensure dirs are created
      try:
        os.makedirs(os.path.dirname(full_path))
        if full_path.endswith('/'):
          # we only want to create directories
          return
      except OSError, err:
        if err.errno == errno.EEXIST:
          # we don't care if the dir already exists
          pass
        else:
          raise
      # create dummy files
      tmp = open(full_path, 'w')
      tmp.close()

  def testTraverse(self):
    """Test that we are traversing the directory properly."""
    dir_structure = ['one/two/test.txt', 'one/blah.py',
                     'three/extra.conf']
    self._CreateNestedDir(dir_structure)

    files = cros_build_lib.ListFiles(self.tempdir)
    for f in files:
      f = f.replace(self.tempdir, '').lstrip('/')
      if f not in dir_structure:
        self.fail('%s was not found in %s' % (f, dir_structure))

  def testEmptyFilePath(self):
    """Test that we return nothing when directories are empty."""
    dir_structure = ['one/', 'two/', 'one/a/']
    self._CreateNestedDir(dir_structure)
    files = cros_build_lib.ListFiles(self.tempdir)
    self.assertEqual(files, [])

  def testNoSuchDir(self):
    try:
      cros_build_lib.ListFiles(os.path.join(self.tempdir, 'missing'))
    except OSError, err:
      self.assertEqual(err.errno, errno.ENOENT)


class HelperMethodSimpleTests(cros_test_lib.TestCase):
  """Tests for various helper methods without using mox."""

  def _TestChromeosVersion(self, test_str, expected=None):
    actual = cros_build_lib.GetChromeosVersion(test_str)
    self.assertEqual(expected, actual)

  def testGetChromeosVersionWithValidVersionReturnsValue(self):
    expected = '0.8.71.2010_09_10_1530'
    test_str = ' CHROMEOS_VERSION_STRING=0.8.71.2010_09_10_1530 '
    self._TestChromeosVersion(test_str, expected)

  def testGetChromeosVersionWithMultipleVersionReturnsFirstMatch(self):
    expected = '0.8.71.2010_09_10_1530'
    test_str = (' CHROMEOS_VERSION_STRING=0.8.71.2010_09_10_1530 '
                ' CHROMEOS_VERSION_STRING=10_1530 ')
    self._TestChromeosVersion(test_str, expected)

  def testGetChromeosVersionWithInvalidVersionReturnsDefault(self):
    test_str = ' CHROMEOS_VERSION_STRING=invalid_version_string '
    self._TestChromeosVersion(test_str)

  def testGetChromeosVersionWithEmptyInputReturnsDefault(self):
    self._TestChromeosVersion('')

  def testGetChromeosVersionWithNoneInputReturnsDefault(self):
    self._TestChromeosVersion(None)


class YNInteraction():
  """Class to hold a list of responses and expected reault of YN prompt."""
  def __init__(self, responses, expected_result):
    self.responses = responses
    self.expected_result = expected_result


class InputTest(cros_test_lib.MoxOutputTestCase):


  def testGetInput(self):
    self.mox.StubOutWithMock(__builtin__, 'raw_input')

    prompt = 'Some prompt'
    response = 'Some response'
    __builtin__.raw_input(prompt).AndReturn(response)
    self.mox.ReplayAll()

    self.assertEquals(response, cros_build_lib.GetInput(prompt))
    self.mox.VerifyAll()

  def _TestYesNoPrompt(self, interactions, prompt_suffix, default, full):
    self.mox.StubOutWithMock(__builtin__, 'raw_input')

    prompt = 'Continue' # Not important
    actual_prompt = '\n%s %s? ' % (prompt, prompt_suffix)

    for interaction in interactions:
      for response in interaction.responses:
        __builtin__.raw_input(actual_prompt).AndReturn(response)
    self.mox.ReplayAll()

    for interaction in interactions:
      retval = cros_build_lib.YesNoPrompt(default, prompt=prompt, full=full)
      msg = ('A %s prompt with %r responses expected result %r, but got %r' %
             (prompt_suffix, interaction.responses,
              interaction.expected_result, retval))
      self.assertEquals(interaction.expected_result, retval, msg=msg)
    self.mox.VerifyAll()

  def testYesNoDefaultNo(self):
    tests = [YNInteraction([''], cros_build_lib.NO),
             YNInteraction(['n'], cros_build_lib.NO),
             YNInteraction(['no'], cros_build_lib.NO),
             YNInteraction(['foo', ''], cros_build_lib.NO),
             YNInteraction(['foo', 'no'], cros_build_lib.NO),
             YNInteraction(['y'], cros_build_lib.YES),
             YNInteraction(['ye'], cros_build_lib.YES),
             YNInteraction(['yes'], cros_build_lib.YES),
             YNInteraction(['foo', 'yes'], cros_build_lib.YES),
             YNInteraction(['foo', 'bar', 'x', 'y'], cros_build_lib.YES),
             ]
    self._TestYesNoPrompt(tests, '(y/N)', cros_build_lib.NO, False)

  def testYesNoDefaultYes(self):
    tests = [YNInteraction([''], cros_build_lib.YES),
             YNInteraction(['n'], cros_build_lib.NO),
             YNInteraction(['no'], cros_build_lib.NO),
             YNInteraction(['foo', ''], cros_build_lib.YES),
             YNInteraction(['foo', 'no'], cros_build_lib.NO),
             YNInteraction(['y'], cros_build_lib.YES),
             YNInteraction(['ye'], cros_build_lib.YES),
             YNInteraction(['yes'], cros_build_lib.YES),
             YNInteraction(['foo', 'yes'], cros_build_lib.YES),
             YNInteraction(['foo', 'bar', 'x', 'y'], cros_build_lib.YES),
             ]
    self._TestYesNoPrompt(tests, '(Y/n)', cros_build_lib.YES, False)

  def testYesNoDefaultNoFull(self):
    tests = [YNInteraction([''], cros_build_lib.NO),
             YNInteraction(['n', ''], cros_build_lib.NO),
             YNInteraction(['no'], cros_build_lib.NO),
             YNInteraction(['foo', ''], cros_build_lib.NO),
             YNInteraction(['foo', 'no'], cros_build_lib.NO),
             YNInteraction(['y', 'yes'], cros_build_lib.YES),
             YNInteraction(['ye', ''], cros_build_lib.NO),
             YNInteraction(['yes'], cros_build_lib.YES),
             YNInteraction(['foo', 'yes'], cros_build_lib.YES),
             YNInteraction(['foo', 'bar', 'y', ''], cros_build_lib.NO),
             YNInteraction(['n', 'y', 'no'], cros_build_lib.NO),
            ]
    self._TestYesNoPrompt(tests, '(yes/No)', cros_build_lib.NO, True)

  def testYesNoDefaultYesFull(self):
    tests = [YNInteraction([''], cros_build_lib.YES),
             YNInteraction(['n', ''], cros_build_lib.YES),
             YNInteraction(['no'], cros_build_lib.NO),
             YNInteraction(['foo', ''], cros_build_lib.YES),
             YNInteraction(['foo', 'no'], cros_build_lib.NO),
             YNInteraction(['y', 'yes'], cros_build_lib.YES),
             YNInteraction(['n', ''], cros_build_lib.YES),
             YNInteraction(['yes'], cros_build_lib.YES),
             YNInteraction(['foo', 'yes'], cros_build_lib.YES),
             YNInteraction(['foo', 'bar', 'n', ''], cros_build_lib.YES),
             YNInteraction(['n', 'y', 'no'], cros_build_lib.NO),
            ]
    self._TestYesNoPrompt(tests, '(Yes/no)', cros_build_lib.YES, True)

  def testSubCommandTimeout(self):
    """Tests that we can nest SubCommandTimeout correctly."""
    with cros_build_lib.SubCommandTimeout(4):
      with cros_build_lib.SubCommandTimeout(3):
        with cros_build_lib.SubCommandTimeout(1):
          self.assertRaises(cros_build_lib.TimeoutError, time.sleep, 5)

        # Should not raise a timeout exception as 3 > 2.
        time.sleep(1)

      # Should raise a timeout exception.
      self.assertRaises(cros_build_lib.TimeoutError, time.sleep, 5)

  def testSubCommandTimeoutNested(self):
    """Tests that we still re-raise an alarm if both are reached."""
    with cros_build_lib.SubCommandTimeout(1):
      try:
        with cros_build_lib.SubCommandTimeout(2):
          self.assertRaises(cros_build_lib.TimeoutError, time.sleep, 1)

      # Craziness to catch nested timeouts.
      except cros_build_lib.TimeoutError:
        pass
      else:
        self.assertTrue(False, 'Should have thrown an exception')


class TestContextManagerStack(cros_test_lib.TestCase):

  def test(self):
    invoked = []
    counter = iter(itertools.count()).next
    def _mk_kls(has_exception=None, exception_kls=None, suppress=False):
      class foon(object):
        marker = counter()
        def __enter__(self):
          return self

        # pylint: disable=E0213
        def __exit__(obj_self, exc_type, exc, traceback):
          invoked.append(obj_self.marker)
          if has_exception is not None:
            self.assertTrue(all(x is not None
                              for x in (exc_type, exc, traceback)))
            self.assertTrue(exc_type == has_exception)
          if exception_kls:
            raise exception_kls()
          if suppress:
            return True
      return foon

    with cros_build_lib.ContextManagerStack() as stack:
      # Note... these tests are in reverse, since the exception
      # winds its way up the stack.
      stack.Add(_mk_kls())
      stack.Add(_mk_kls(ValueError, suppress=True))
      stack.Add(_mk_kls(IndexError, exception_kls=ValueError))
      stack.Add(_mk_kls(IndexError))
      stack.Add(_mk_kls(exception_kls=IndexError))
      stack.Add(_mk_kls())
    self.assertEqual(invoked, list(reversed(range(6))))


class TestManifestCheckout(cros_test_lib.TempDirTestCase):

  def setUp(self):
    self.manifest_dir = os.path.join(self.tempdir, '.repo', 'manifests')
    # Initialize a repo intance here.
    # TODO(vapier, ferringb):  mangle this so it inits from a local
    # checkout if one is available, same for the git-repo fetch.
    cmd = ['repo', 'init', '-u', constants.MANIFEST_URL,]
    cros_build_lib.RunCommandCaptureOutput(cmd, cwd=self.tempdir, input='')
    self.active_manifest = os.path.realpath(
        os.path.join(self.tempdir, '.repo', 'manifest.xml'))

  def testManifestInheritance(self):
    osutils.WriteFile(self.active_manifest, """
        <manifest>
          <include name="include-target.xml" />
          <include name="empty.xml" />
          <project name="monkeys" remote="foon" revision="master" />
        </manifest>""")
    # First, verify it properly explodes if the include can't be found.
    self.assertRaises(EnvironmentError,
                      cros_build_lib.ManifestCheckout, self.tempdir)

    # Next, verify it can read an empty manifest; this is to ensure
    # that we can point Manifest at the empty manifest without exploding,
    # same for ManifestCheckout; this sort of thing is primarily useful
    # to ensure no step of an include assumes everything is yet assembled.
    empty_path = os.path.join(self.manifest_dir, 'empty.xml')
    osutils.WriteFile(empty_path, '<manifest/>')
    cros_build_lib.Manifest(empty_path)
    cros_build_lib.ManifestCheckout(self.tempdir, manifest_path=empty_path)

    # Next, verify include works.
    osutils.WriteFile(os.path.join(self.manifest_dir, 'include-target.xml'),
        """
        <manifest>
          <remote name="foon" fetch="http://localhost" />
        </manifest>""")
    manifest = cros_build_lib.ManifestCheckout(self.tempdir)
    self.assertEqual(list(manifest.projects), ['monkeys'])
    self.assertEqual(list(manifest.remotes), ['foon'])

  # pylint: disable=E1101
  def testGetManifestsBranch(self):
    func = cros_build_lib.ManifestCheckout._GetManifestsBranch
    manifest = self.manifest_dir
    repo_root = self.tempdir

    # pylint: disable=W0613
    def reconfig(merge='master', origin='origin'):
      if merge is not None:
        merge = 'refs/heads/%s' % merge
      for key in ('merge', 'origin'):
        val = locals()[key]
        key = 'branch.default.%s' % key
        if val is None:
          cros_build_lib.RunGitCommand(manifest, ['config', '--unset', key],
                                       error_code_ok=True)
        else:
          cros_build_lib.RunGitCommand(manifest, ['config', key, val])

    # First, verify our assumptions about a fresh repo init are correct.
    self.assertEqual('default', cros_build_lib.GetCurrentBranch(manifest))
    self.assertEqual('master', func(repo_root))

    # Ensure we can handle a missing origin; this can occur jumping between
    # branches, and can be worked around.
    reconfig(origin=None)
    self.assertEqual('default', cros_build_lib.GetCurrentBranch(manifest))
    self.assertEqual('master', func(repo_root))

    # TODO(ferringb): convert this over to assertRaises2
    def assertExcept(message, **kwds):
      reconfig(**kwds)
      try:
        func(repo_root)
        assert "Testing for %s, an exception wasn't thrown." % (message,)
      except OSError, e:
        self.assertEqual(e.errno, errno.ENOENT)
        self.assertTrue(message in str(e),
                        msg="Couldn't find string %r in error message %r"
                        % (message, str(e)))

    # No merge target means the configuration isn't usable, period.
    assertExcept("git tracking configuration for that branch is broken",
                 merge=None)

    # Ensure we detect if we're on the wrong branch, even if it has
    # tracking setup.
    cros_build_lib.RunGitCommand(
        manifest, ['checkout', '-t', 'origin/master', '-b', 'test'])
    assertExcept("It should be checked out to 'default'")

    # Ensure we handle detached HEAD w/ an appropriate exception.
    cros_build_lib.RunGitCommand(manifest, ['checkout', '--detach', 'test'])
    assertExcept("It should be checked out to 'default'")

    # Finally, ensure that if the default branch is non-existant, we still throw
    # a usable exception.
    cros_build_lib.RunGitCommand(manifest, ['branch', '-d', 'default'])
    assertExcept("It should be checked out to 'default'")


if __name__ == '__main__':
  cros_test_lib.main()
