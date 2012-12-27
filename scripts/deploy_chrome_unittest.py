#!/usr/bin/python
# Copyright (c) 2012 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                '..', '..'))
from chromite.lib import cros_build_lib
from chromite.lib import cros_test_lib
from chromite.lib import partial_mock
from chromite.lib import remote_access_unittest
from chromite.scripts import deploy_chrome

# TODO(build): Finish test wrapper (http://crosbug.com/37517).
# Until then, this has to be after the chromite imports.
import mock


# pylint: disable=W0212

_REGULAR_TO = ('--to', 'monkey')
_GS_PATH = 'gs://foon'


def _ParseCommandLine(argv):
  return deploy_chrome._ParseCommandLine(['--log-level', 'debug'] + argv)


class InterfaceTest(cros_test_lib.OutputTestCase):
  """Tests the commandline interface of the script."""

  def testGsLocalPathUnSpecified(self):
    """Test no chrome path specified."""
    with self.OutputCapturer():
      self.assertRaises2(SystemExit, _ParseCommandLine, list(_REGULAR_TO),
                         check_attrs={'code': 2})

  def testGsPathSpecified(self):
    """Test case of GS path specified."""
    argv = list(_REGULAR_TO) + ['--gs-path', _GS_PATH]
    _ParseCommandLine(argv)

  def testLocalPathSpecified(self):
    """Test case of local path specified."""
    argv =  list(_REGULAR_TO) + ['--local-pkg-path', '/path/to/chrome']
    _ParseCommandLine(argv)

  def testNoTarget(self):
    """Test no target specified."""
    argv = ['--gs-path', _GS_PATH]
    with self.OutputCapturer():
      self.assertRaises2(SystemExit, _ParseCommandLine, argv,
                         check_attrs={'code': 2})


class DeployChromeMock(partial_mock.PartialMock):

  TARGET = 'chromite.scripts.deploy_chrome.DeployChrome'
  ATTRS = ('_CheckRootfsWriteable', '_DisableRootfsVerification',
           '_KillProcsIfNeeded')

  def __init__(self, disable_ok=True):
    partial_mock.PartialMock.__init__(self)
    self.disable_ok = disable_ok
    self.rootfs_writeable = False
    # Target starts off as having rootfs verification enabled.
    self.rsh_mock = remote_access_unittest.RemoteShMock()
    self.MockMountCmd(1)

  def MockMountCmd(self, returnvalue):
    def hook(_inst, *_args, **_kwargs):
      self.rootfs_writeable = True

    self.rsh_mock.AddCmdResult(deploy_chrome.MOUNT_RW_COMMAND,
                               returnvalue,
                               side_effect=None if returnvalue else hook)

  def PreStart(self):
    self.rsh_mock.start()

  def PreStop(self):
    self.rsh_mock.stop()

  def _CheckRootfsWriteable(self, _inst):
    return self.rootfs_writeable

  def _DisableRootfsVerification(self, _inst):
    self.MockMountCmd(int(not self.disable_ok))

  def _KillProcsIfNeeded(self, _inst):
    # Fully stub out for now.
    pass


class DeployChromeTest(cros_test_lib.MockTempDirTestCase):

  def _GetDeployChrome(self):
    options, _ = _ParseCommandLine(list(_REGULAR_TO) + ['--gs-path', _GS_PATH])
    return deploy_chrome.DeployChrome(
        options, self.tempdir, os.path.join(self.tempdir, 'staging'))

  def setUp(self):
    self.deploy_mock = DeployChromeMock()
    self.StartPatcher(self.deploy_mock)
    self.deploy = self._GetDeployChrome()


class TestPrepareTarget(DeployChromeTest):
  """Testing disabling of rootfs verification and RO mode."""

  def testSuccess(self):
    """Test the working case."""
    self.deploy._PrepareTarget()

  def testDisableRootfsVerificationFailure(self):
    """Test failure to disable rootfs verification."""
    self.deploy_mock.disable_ok = False
    self.assertRaises(cros_build_lib.RunCommandError,
                      self.deploy._PrepareTarget)

  def testMountRwFailure(self):
    """The mount command returncode was 0 but rootfs is still readonly."""
    with mock.patch.object(deploy_chrome.DeployChrome, '_CheckRootfsWriteable',
                           auto_spec=True) as m:
      m.return_value = False
      self.assertRaises(SystemExit, self.deploy._PrepareTarget)

  def testMountRwSuccessFirstTime(self):
    """We were able to mount as RW the first time."""
    self.deploy_mock.MockMountCmd(0)
    self.deploy._PrepareTarget()


PROC_MOUNTS = """\
rootfs / rootfs rw 0 0
/dev/root / ext2 %s,relatime,user_xattr,acl 0 0
devtmpfs /dev devtmpfs rw,relatime,size=970032k,nr_inodes=242508,mode=755 0 0
none /proc proc rw,nosuid,nodev,noexec,relatime 0 0
"""


class TestCheckRootfs(DeployChromeTest):
  """Test Rootfs RW check functionality."""

  def setUp(self):
    self.deploy_mock.UnMockAttr('_CheckRootfsWriteable')

  def MockProcMountsCmd(self, output):
    self.deploy_mock.rsh_mock.AddCmdResult('cat /proc/mounts', output=output)

  def testCheckRootfsWriteableFalse(self):
    """Correct results with RO."""
    self.MockProcMountsCmd(PROC_MOUNTS % 'ro')
    self.assertFalse(self.deploy._CheckRootfsWriteable())

  def testCheckRootfsWriteableTrue(self):
    """Correct results with RW."""
    self.MockProcMountsCmd(PROC_MOUNTS % 'rw')
    self.assertTrue(self.deploy._CheckRootfsWriteable())


class TestUiJobStarted(DeployChromeTest):
  """Test detection of a running 'ui' job."""

  def MockStatusUiCmd(self, output):
    self.deploy_mock.rsh_mock.AddCmdResult('status ui', output=output)

  def testUiJobStartedFalse(self):
    """Correct results with a stopped job."""
    self.MockStatusUiCmd('ui stop/waiting')
    self.assertFalse(self.deploy._CheckUiJobStarted())

  def testCheckRootfsWriteableTrue(self):
    """Correct results with a running job."""
    self.MockStatusUiCmd('ui start/running, process 297')
    self.assertTrue(self.deploy._CheckUiJobStarted())


if __name__ == '__main__':
  cros_test_lib.main()
