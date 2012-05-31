#!/usr/bin/python

# Copyright (c) 2012 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unittests for commands.  Needs to be run inside of chroot for mox."""

import mox
import os
import shutil
import sys
import tempfile
import unittest

import constants
sys.path.insert(0, constants.SOURCE_ROOT)
from chromite.buildbot import cbuildbot_commands as commands
from chromite.buildbot import configure_repo
from chromite.lib import cros_build_lib
from chromite.lib import cros_test_lib


# pylint: disable=W0212,R0904
class CBuildBotTest(mox.MoxTestBase):

  def setUp(self):
    mox.MoxTestBase.setUp(self)
    # Always stub RunCommmand out as we use it in every method.
    self.mox.StubOutWithMock(cros_build_lib, 'RunCommand')
    self._test_repos = [['kernel', 'third_party/kernel/files'],
                        ['login_manager', 'platform/login_manager']
                       ]
    self._test_cros_workon_packages = (
        'chromeos-base/kernel\nchromeos-base/chromeos-login\n')
    self._test_board = 'test-board'
    self._buildroot = '.'
    self._test_dict = {'kernel': ['chromos-base/kernel', 'dev-util/perf'],
                       'cros': ['chromos-base/libcros']
                      }
    self._test_string = 'kernel.git@12345test cros.git@12333test'
    self._test_string += ' crosutils.git@blahblah'
    self._revision_file = 'test-revisions.pfq'
    self._test_parsed_string_array = [['chromeos-base/kernel', '12345test'],
                                      ['dev-util/perf', '12345test'],
                                      ['chromos-base/libcros', '12345test']]
    self._overlays = ['%s/src/third_party/chromiumos-overlay' % self._buildroot]
    self._chroot_overlays = [
        cros_build_lib.ReinterpretPathForChroot(p) for p in self._overlays
    ]
    self._CWD = os.path.dirname(os.path.realpath(__file__))
    self._work_dir = tempfile.mkdtemp()
    os.makedirs(self._work_dir + '/chroot/tmp/taco')

  def tearDown(self):
    shutil.rmtree(self._work_dir)

  def testRunTestSuite(self):
    """Tests if we can parse the test_types so that sane commands are called."""
    def ItemsNotInList(items, list_):
      """Helper function that returns whether items are not in a list."""
      return set(items).isdisjoint(set(list_))

    cwd = self._work_dir + '/src/scripts'

    obj = cros_test_lib.EasyAttr(returncode=0)

    cros_build_lib.RunCommand(
        mox.Func(lambda x: ItemsNotInList(['--quick', '--only_verify'], x)),
        cwd=cwd, error_ok=True).AndReturn(obj)

    self.mox.ReplayAll()
    commands.RunTestSuite(self._work_dir, self._test_board, self._buildroot,
                          '/tmp/taco', build_config='test_config',
                          whitelist_chrome_crashes=False,
                          test_type=constants.FULL_AU_TEST_TYPE)
    self.mox.VerifyAll()
    self.mox.ResetAll()

    cros_build_lib.RunCommand(mox.In('--quick'), cwd=cwd,
                              error_ok=True).AndReturn(obj)

    self.mox.ReplayAll()
    commands.RunTestSuite(self._work_dir, self._test_board, self._buildroot,
                          '/tmp/taco', build_config='test_config',
                          whitelist_chrome_crashes=False,
                          test_type=constants.SIMPLE_AU_TEST_TYPE)
    self.mox.VerifyAll()
    self.mox.ResetAll()

    cros_build_lib.RunCommand(
        mox.And(mox.In('--quick'), mox.In('--only_verify')),
        cwd=cwd, error_ok=True).AndReturn(obj)

    self.mox.ReplayAll()
    commands.RunTestSuite(self._work_dir, self._test_board, self._buildroot,
                          '/tmp/taco', build_config='test_config',
                          whitelist_chrome_crashes=False,
                          test_type=constants.SMOKE_SUITE_TEST_TYPE)
    self.mox.VerifyAll()

  def testArchiveTestResults(self):
    """Test if we can archive the latest results dir to Google Storage."""
    # Set vars for call.
    self.mox.StubOutWithMock(shutil, 'rmtree')
    buildroot = '/fake_dir'
    test_tarball = os.path.join(buildroot, 'test_results.tgz')
    test_results_dir = 'fake_results_dir'

    # Convenience variables to make archive easier to understand.
    path_to_results = os.path.join(buildroot, 'chroot', test_results_dir)

    cros_build_lib.SudoRunCommand(
        ['chmod', '-R', 'a+rw', path_to_results], print_cmd=False)
    cros_build_lib.RunCommand(
        ['tar', 'czf', test_tarball, '--directory=%s' % path_to_results, '.'],
        print_cmd=False)
    shutil.rmtree(path_to_results)
    self.mox.ReplayAll()
    commands.ArchiveTestResults(buildroot, test_results_dir, '')
    self.mox.VerifyAll()

  def testGenerateMinidumpStackTraces(self):
    """Test if we can generate stack traces for minidumps."""
    temp_dir = '/chroot/temp_dir'
    gzipped_test_tarball = '/test_results.tgz'
    test_tarball = '/test_results.tar'
    dump_file = os.path.join(temp_dir, 'test.dmp')
    buildroot = '/'
    board = 'test_board'
    symbol_dir = os.path.join('/build', board, 'usr', 'lib', 'debug',
                              'breakpad')
    cwd = os.path.join(buildroot, 'src', 'scripts')
    archive_dir = '/archive/dir'

    self.mox.StubOutWithMock(tempfile, 'mkdtemp')
    tempfile.mkdtemp(dir=mox.IgnoreArg(), prefix=mox.IgnoreArg()). \
        AndReturn(temp_dir)
    self.mox.StubOutWithMock(os, 'walk')
    dump_file_dir, dump_file_name = os.path.split(dump_file)
    os.walk(mox.IgnoreArg()).AndReturn([(dump_file_dir, [''],
                                       [dump_file_name])])
    self.mox.StubOutWithMock(cros_build_lib, 'ReinterpretPathForChroot')
    cros_build_lib.ReinterpretPathForChroot(
        mox.IgnoreArg()).AndReturn(dump_file)
    self.mox.StubOutWithMock(commands, 'ArchiveFile')
    self.mox.StubOutWithMock(os, 'unlink')
    self.mox.StubOutWithMock(shutil, 'rmtree')

    cros_build_lib.RunCommand(['gzip', '-df', gzipped_test_tarball])
    cros_build_lib.RunCommand(
        ['tar',
         'xf',
         test_tarball,
         '--directory=%s' % temp_dir,
         '--wildcards', '*.dmp'],
         error_ok=True,
         redirect_stderr=True).AndReturn(cros_build_lib.CommandResult())
    stack_trace = '%s.txt' % dump_file
    cros_build_lib.RunCommand(
        ['minidump_stackwalk', dump_file, symbol_dir], cwd=cwd,
        enter_chroot=True, error_ok=True, log_stdout_to_file=stack_trace,
        redirect_stderr=True)
    commands.ArchiveFile(stack_trace, archive_dir)
    cros_build_lib.RunCommand(
        ['tar', 'uf', test_tarball, '--directory=%s' % temp_dir, '.'])
    cros_build_lib.RunCommand(
        'gzip -c %s > %s' % (test_tarball, gzipped_test_tarball), shell=True)
    os.unlink(test_tarball)
    shutil.rmtree(temp_dir)

    self.mox.ReplayAll()
    commands.GenerateMinidumpStackTraces(buildroot, board,
                                         gzipped_test_tarball,
                                         archive_dir)
    self.mox.VerifyAll()

  def testUprevAllPackages(self):
    """Test if we get None in revisions.pfq indicating Full Builds."""
    drop_file = commands._PACKAGE_FILE % {'buildroot': self._buildroot}
    cros_build_lib.RunCommand(
        ['../../chromite/bin/cros_mark_as_stable', '--all',
         '--boards=%s' % self._test_board,
         '--overlays=%s' % ':'.join(self._chroot_overlays),
         '--drop_file=%s' % cros_build_lib.ReinterpretPathForChroot(drop_file),
         'commit'],
        cwd='%s/src/scripts' % self._buildroot,
        enter_chroot=True)

    self.mox.ReplayAll()
    commands.UprevPackages(self._buildroot,
                           [self._test_board],
                           self._overlays)
    self.mox.VerifyAll()

  def testUploadPublicPrebuilts(self):
    """Test _UploadPrebuilts with a public location."""
    buildnumber = 4
    check = mox.And(mox.IsA(list),
                    mox.In('gs://chromeos-prebuilt'),
                    mox.In(constants.PFQ_TYPE))
    cros_build_lib.RunCommand(check, cwd=os.path.dirname(commands.__file__))
    self.mox.ReplayAll()
    commands.UploadPrebuilts(self._buildroot, self._test_board, False,
                             constants.PFQ_TYPE, None)
    self.mox.VerifyAll()

  def testUploadPrivatePrebuilts(self):
    """Test _UploadPrebuilts with a private location."""
    buildnumber = 4
    check = mox.And(mox.IsA(list),
                    mox.In('gs://chromeos-prebuilt'),
                    mox.In(constants.PFQ_TYPE))
    cros_build_lib.RunCommand(check, cwd=os.path.dirname(commands.__file__))
    self.mox.ReplayAll()
    commands.UploadPrebuilts(self._buildroot, self._test_board, True,
                             constants.PFQ_TYPE, None)
    self.mox.VerifyAll()

  def testChromePrebuilts(self):
    """Test _UploadPrebuilts for Chrome prebuilts."""
    buildnumber = 4
    check = mox.And(mox.IsA(list),
                    mox.In('gs://chromeos-prebuilt'),
                    mox.In(constants.CHROME_PFQ_TYPE))
    cros_build_lib.RunCommand(check, cwd=os.path.dirname(commands.__file__))
    self.mox.ReplayAll()
    commands.UploadPrebuilts(self._buildroot, self._test_board, False,
                             constants.CHROME_PFQ_TYPE, 'tot')
    self.mox.VerifyAll()


  def testBuildMinimal(self):
    """Base case where Build is called with minimal options."""
    buildroot = '/bob/'
    cmd = ['./build_packages', '--nowithautotest',
           '--board=x86-generic'] + commands._LOCAL_BUILD_FLAGS
    cros_build_lib.RunCommand(mox.SameElementsAs(cmd),
                              cwd=mox.StrContains(buildroot),
                              chroot_args=[],
                              enter_chroot=True,
                              extra_env={})
    self.mox.ReplayAll()
    commands.Build(buildroot=buildroot,
                   board='x86-generic',
                   build_autotest=False,
                   usepkg=False,
                   skip_toolchain_update=False,
                   nowithdebug=False,
                   )
    self.mox.VerifyAll()

  def testBuildMaximum(self):
    """Base case where Build is called with all options (except extra_evn)."""
    buildroot = '/bob/'
    arg_test = mox.SameElementsAs(['./build_packages',
                                   '--board=x86-generic',
                                   '--skip_toolchain_update',
                                   '--nowithdebug'])
    cros_build_lib.RunCommand(arg_test,
                              cwd=mox.StrContains(buildroot),
                              chroot_args=[],
                              enter_chroot=True,
                              extra_env={})
    self.mox.ReplayAll()
    commands.Build(buildroot=buildroot,
                   board='x86-generic',
                   build_autotest=True,
                   usepkg=True,
                   skip_toolchain_update=True,
                   nowithdebug=True,
                   )
    self.mox.VerifyAll()

  def testBuildWithEnv(self):
    """Case where Build is called with a custom environment."""
    buildroot = '/bob/'
    extra = {'A' :'Av', 'B' : 'Bv'}
    cros_build_lib.RunCommand(
        mox.IgnoreArg(),
        cwd=mox.StrContains(buildroot),
        chroot_args=[],
        enter_chroot=True,
        extra_env=mox.And(
            mox.ContainsKeyValue('A', 'Av'), mox.ContainsKeyValue('B', 'Bv')))
    self.mox.ReplayAll()
    commands.Build(buildroot=buildroot,
                   board='x86-generic',
                   build_autotest=False,
                   usepkg=False,
                   skip_toolchain_update=False,
                   nowithdebug=False,
                   extra_env=extra)
    self.mox.VerifyAll()

  def testUploadSymbols(self):
    """Test UploadSymbols Command."""
    buildroot = '/bob'
    board = 'board_name'

    cros_build_lib.RunCommand(
        ['./upload_symbols', '--board=board_name', '--yes', '--verbose',
         '--official_build'], cwd='/bob/src/scripts', error_ok=True,
        enter_chroot=True)

    cros_build_lib.RunCommand(
        ['./upload_symbols', '--board=board_name', '--yes', '--verbose'],
        cwd='/bob/src/scripts', error_ok=True, enter_chroot=True)

    self.mox.ReplayAll()
    commands.UploadSymbols(buildroot, board, official=True)
    commands.UploadSymbols(buildroot, board, official=False)
    self.mox.VerifyAll()

  def testPushImages(self):
    """Test PushImages Command."""
    buildroot = '/bob'
    board = 'board_name'
    branch_name = 'branch_name'
    archive_url = 'gs://archive/url'

    cros_build_lib.RunCommand(
        ['./pushimage', '--board=board_name', '--branch=branch_name',
         archive_url], cwd=mox.StrContains('crostools'))

    self.mox.ReplayAll()
    commands.PushImages(buildroot, board, branch_name, archive_url, None)
    self.mox.VerifyAll()

  def testPushImages2(self):
    """Test PushImages Command with profile."""
    buildroot = '/bob'
    board = 'board_name'
    branch_name = 'branch_name'
    profile_name = 'profile_name'
    archive_url = 'gs://archive/url'

    cros_build_lib.RunCommand(
        ['./pushimage', '--board=board_name', '--profile=profile_name',
         '--branch=branch_name', archive_url], cwd=mox.StrContains('crostools'))

    self.mox.ReplayAll()
    commands.PushImages(buildroot, board, branch_name, archive_url,
                        profile=profile_name)
    self.mox.VerifyAll()


if __name__ == '__main__':
  unittest.main()
