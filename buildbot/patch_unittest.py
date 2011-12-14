#!/usr/bin/python

# Copyright (c) 2011 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unittests for commands.  Needs to be run inside of chroot for mox."""

import logging
import mox
import os
import sys
import tempfile
import unittest

import constants
sys.path.append(constants.SOURCE_ROOT)
from chromite.lib import cros_build_lib as cros_lib
from chromite.buildbot import patch as cros_patch
from chromite.buildbot import gerrit_helper


# pylint: disable=W0212,R0904
class GerritQueryTests(mox.MoxTestBase):

  def setUp(self):
    mox.MoxTestBase.setUp(self)
    result = ('{"project":"chromiumos/chromite","branch":"master","id":'
             '"Icb8e1d315d465a077ffcddd7d1ab2307573017d5","number":"2144",'
             '"subject":"Add functionality to cbuildbot to patch in a set '
             'of Gerrit CL\u0027s","owner":{"name":"Ryan Cui","email":'
             '"rcui@chromium.org"},"url":'
             '"http://gerrit.chromium.org/gerrit/2144","lastUpdated":'
             '1307577655,"sortKey":"00158e2000000860","open":true,"status":'
             '"NEW","currentPatchSet":{"number":"3",'
             '"revision":"b1c82d0f1c916b7f66cfece625d67fb5ecea9ea7","ref":'
             '"refs/changes/44/2144/3","uploader":{"name":"Ryan Cui","email":'
             '"rcui@chromium.org"}}}\n'
             '{"type":"stats","rowCount":1,"runTimeMilliseconds":4}')

    self.result = result
    self.mox.StubOutWithMock(cros_lib, 'RunCommand')

  def testPatchInfoNotFound(self):
    """Test case where ChangeID isn't found on internal server."""
    patches = ['1A3G2D1D2']

    output_obj = cros_lib.CommandResult()
    output_obj.returncode = 0
    output_obj.output = ('{"type":"error",'
                         '"message":"Unsupported query:5S2D4D2D4"}')

    cros_lib.RunCommand(mox.In('gerrit.chromium.org'),
                        redirect_stdout=True).AndReturn(output_obj)

    self.mox.ReplayAll()

    self.assertRaises(cros_patch.PatchException, cros_patch.GetGerritPatchInfo,
                      patches)
    self.mox.VerifyAll()


  def testGetInternalPatchInfo(self):
    """Test case where ChangeID is for an internal CL."""
    patches = ['*1A3G2D1D2']

    output_obj = cros_lib.CommandResult()
    output_obj.returncode = 0
    output_obj.output = self.result

    cros_lib.RunCommand(mox.In('gerrit-int.chromium.org'),
                        redirect_stdout=True).AndReturn(output_obj)

    self.mox.ReplayAll()

    patch_info = cros_patch.GetGerritPatchInfo(patches)
    self.assertEquals(patch_info[0].internal, True)
    self.mox.VerifyAll()

  def testGetExternalPatchInfo(self):
    """Test case where ChangeID is for an external CL."""
    patches = ['1A3G2D1D2']

    output_obj = cros_lib.CommandResult()
    output_obj.returncode = 0
    output_obj.output = self.result

    cros_lib.RunCommand(mox.In('gerrit.chromium.org'),
                        redirect_stdout=True).AndReturn(output_obj)

    self.mox.ReplayAll()

    patch_info = cros_patch.GetGerritPatchInfo(patches)
    self.assertEquals(patch_info[0].internal, False)
    self.mox.VerifyAll()

  def testPatchInfoParsing(self):
    """Test parsing of the JSON results."""
    patches = ['1A3G2D1D2']

    output_obj = cros_lib.CommandResult()
    output_obj.returncode = 0
    output_obj.output = self.result

    cros_lib.RunCommand(mox.In('gerrit.chromium.org'),
                        redirect_stdout=True).AndReturn(output_obj)

    self.mox.ReplayAll()

    patch_info = cros_patch.GetGerritPatchInfo(patches)
    self.assertEquals(patch_info[0].project, 'chromiumos/chromite')
    self.assertEquals(patch_info[0].ref, 'refs/changes/44/2144/3')

    self.mox.VerifyAll()


class GerritPatchTest(mox.MoxTestBase):
  FAKE_PATCH_JSON = {
    "project":"tacos/chromite", "branch":"master",
    "id":"Iee5c89d929f1850d7d4e1a4ff5f21adda800025f",
    "currentPatchSet": {
      "number":"2", "ref":"refs/changes/72/5172/1",
      "revision":"ff10979dd360e75ff21f5cf53b7f8647578785ef",
    },
    "number":"1112",
    "subject":"chromite commit",
    "owner":{"name":"Chromite Master", "email":"chromite@chromium.org"},
    "url":"http://gerrit.chromium.org/gerrit/1112",
    "lastUpdated":1311024529,
    "sortKey":"00166e8700001052",
    "open": True,
    "status":"NEW",
  }

  def setUp(self):
    mox.MoxTestBase.setUp(self)
    self.mox.StubOutWithMock(cros_patch.GerritPatch, 'RemoveCommitReady')

  def testGerritSubmit(self):
    """Tests submission review string looks correct."""
    self.mox.StubOutWithMock(cros_lib, 'RunCommand')
    my_patch = cros_patch.GerritPatch(self.FAKE_PATCH_JSON, False)
    helper = gerrit_helper.GerritHelper(False)
    cros_lib.RunCommand(
        'ssh -p 29418 gerrit.chromium.org gerrit review '
        '--submit 1112,2'.split(), error_ok=True)
    self.mox.ReplayAll()
    my_patch.Submit(helper, False)
    self.mox.VerifyAll()

  def testGerritHandleApplied(self):
    """Tests review string looks correct."""
    my_patch = cros_patch.GerritPatch(self.FAKE_PATCH_JSON, False)
    helper = gerrit_helper.GerritHelper(False)
    self.mox.ReplayAll()
    my_patch.HandleApplied(helper, 'http://fake%20url/1234', True)
    self.mox.VerifyAll()


  def testGerritHandleApplyError(self):
    """Tests review string looks correct."""
    my_patch = cros_patch.GerritPatch(self.FAKE_PATCH_JSON, False)
    helper = gerrit_helper.GerritHelper(False)
    my_patch.RemoveCommitReady(helper, True)
    self.mox.ReplayAll()
    my_patch.HandleCouldNotApply(helper, 'http://fake%20url/1234', True)
    self.mox.VerifyAll()

  def testGerritHandleSubmitError(self):
    """Tests review string looks correct."""
    my_patch = cros_patch.GerritPatch(self.FAKE_PATCH_JSON, False)
    helper = gerrit_helper.GerritHelper(False)
    my_patch.RemoveCommitReady(helper, True)
    self.mox.ReplayAll()
    my_patch.HandleCouldNotSubmit(helper, 'http://fake%20url/1234', True)
    self.mox.VerifyAll()

  def testGerritHandleVerifyError(self):
    """Tests review string looks correct."""
    my_patch = cros_patch.GerritPatch(self.FAKE_PATCH_JSON, False)
    helper = gerrit_helper.GerritHelper(False)
    my_patch.RemoveCommitReady(helper, True)
    self.mox.ReplayAll()
    my_patch.HandleCouldNotVerify(helper, 'http://fake%20url/1234', True)
    self.mox.VerifyAll()

  def GerritDepenedenciesHelper(self, git_log, expected_return_tuple):
    build_root = 'fake_build_root'
    project_dir = 'fake_build_root/fake_project_dir'
    self.mox.StubOutWithMock(cros_lib, 'RunCommand')
    self.mox.StubOutWithMock(cros_patch, '_GetProjectManifestBranch')
    self.mox.StubOutWithMock(cros_lib, 'GetProjectDir')

    my_patch = cros_patch.GerritPatch(self.FAKE_PATCH_JSON, False)
    cros_lib.GetProjectDir(build_root, 'tacos/chromite').AndReturn(project_dir)
    # Ignore git fetch.
    cros_lib.RunCommand(mox.IgnoreArg(), cwd=project_dir)
    cros_patch._GetProjectManifestBranch(
        build_root, 'tacos/chromite').AndReturn('m/master')
    cros_lib.RunCommand(
        ['git', 'log', '-z', 'm/master..FETCH_HEAD^'], cwd=project_dir,
        redirect_stdout=True).AndReturn(git_log)

    self.mox.ReplayAll()
    deps = my_patch.GerritDependencies(build_root)
    self.mox.VerifyAll()

    self.assertEqual(deps, expected_return_tuple)

  def PaladinDepenedenciesHelper(self, commit_msg, expected_return_tuple):
    build_root = 'fake_build_root'
    self.mox.StubOutWithMock(cros_patch.GerritPatch, 'CommitMessage')

    my_patch = cros_patch.GerritPatch(self.FAKE_PATCH_JSON, False)
    my_patch.CommitMessage(build_root).AndReturn(commit_msg)

    self.mox.ReplayAll()
    deps = my_patch.PaladinDependencies(build_root)
    self.mox.VerifyAll()

    self.assertEqual(deps, expected_return_tuple)

  def testGerritDependencies(self):
    """Tests that we can get dependencies from a commit with 2 dependencies."""
    commit1 = """
    commit abcdefgh

    Author: Fake person
    Date:  Tue Oct 99

    I am the first commit.

    Change-Id: 1234abcd
    """
    commit2 = """commit abcdefgi
    Author: Fake person
    Date:  Tue Oct 99

    I am the first commit.

    Change-Id: 1234abce
    """
    git_log = self.mox.CreateMock(cros_lib.CommandResult)
    git_log.output = '\0'.join([commit1, commit2])
    self.GerritDepenedenciesHelper(git_log, ['1234abcd', '1234abce'])

  def testGerritNoDependencies(self):
    """Tests that we return an empty tuple if the commit has no deps."""
    git_rev_list_obj = self.mox.CreateMock(cros_lib.CommandResult)
    git_rev_list_obj.output = ''
    self.GerritDepenedenciesHelper(git_rev_list_obj, [])

  def testPaladinDependencies(self):
    """Tests that we can get dependencies specified through commit message."""
    commit_msg = """
    commit abcdefgh

    Author: Fake person
    Date:  Tue Oct 99

    I am the first commit.

    CQ-DEPEND=12345 12356   , 12357
    CQ-DEPEND=123457a

    Change-Id: Iee5c89d929f1850d7d4e1a4ff5f21adda800025f
    """
    self.PaladinDepenedenciesHelper(commit_msg, ['12345', '12356', '12357',
                                                 '123457a'])

  def NotestMockRemoveCommitReady(self):
    """Tests against sosa's test patch to remove Commit Ready bit on failure."""
    my_patch = cros_patch.GerritPatch(self.FAKE_PATCH_JSON, False)
    my_patch.gerrit_number = 8366 # Sosa's test change.
    my_patch.patch_number = 1 # Sosa's test patch.
    helper = gerrit_helper.GerritHelper(False)
    my_patch.HandleCouldNotVerify(helper, 'some_url', False)


class PrepareLocalPatchesTests(mox.MoxTestBase):

  def setUp(self):
    mox.MoxTestBase.setUp(self)

    self.patches = ['my/project:mybranch']

    self.mox.StubOutWithMock(tempfile, 'mkdtemp')
    self.mox.StubOutWithMock(os, 'listdir')
    self.mox.StubOutWithMock(cros_lib, 'GetProjectDir')
    self.mox.StubOutWithMock(cros_lib, 'GetCurrentBranch')
    self.mox.StubOutWithMock(cros_patch, '_GetRemoteTrackingBranch')
    self.mox.StubOutWithMock(cros_lib, 'RunCommand')

    tempfile.mkdtemp(prefix=mox.IgnoreArg()).AndReturn('/tmp/trybot1')
    os.listdir(mox.IgnoreArg()).AndReturn('test.patch')

  def VerifyPatchInfo(self, patch_info, project, branch, tracking_branch):
    """Check the returned LocalPatchInfo against golden values."""
    self.assertEquals(patch_info.project, project)
    self.assertEquals(patch_info.local_branch, branch)
    self.assertEquals(patch_info.tracking_branch, tracking_branch)

  def testBranchSpecifiedSuccessRun(self):
    """Test success with branch specified by user."""
    cros_lib.GetProjectDir(mox.IgnoreArg(), 'my/project').AndReturn('mydir')
    cros_lib.RunCommand(mox.In('m/master..mybranch'),
                        redirect_stdout=mox.IgnoreArg(), cwd='mydir')
    cros_patch._GetRemoteTrackingBranch('mydir',
                                   'mybranch').AndReturn('tracking_branch')
    self.mox.ReplayAll()

    patch_info = cros_patch.PrepareLocalPatches(self.patches, 'master')
    self.VerifyPatchInfo(patch_info[0], 'my/project', 'mybranch',
                         'tracking_branch')
    self.mox.VerifyAll()

  def testNoTrackingBranch(self):
    """Test when project branch does not track a remote branch."""
    cros_lib.GetProjectDir(mox.IgnoreArg(), 'my/project').AndReturn('mydir')
    cros_lib.RunCommand(mox.In('m/master..mybranch'),
                        redirect_stdout=mox.IgnoreArg(), cwd='mydir')
    cros_patch._GetRemoteTrackingBranch(
        'mydir',
        'mybranch').AndRaise(cros_lib.NoTrackingBranchException('error'))
    self.mox.ReplayAll()

    self.assertRaises(cros_patch.PatchException, cros_patch.PrepareLocalPatches,
                      self.patches, 'master')
    self.mox.VerifyAll()


class ApplyLocalPatchesTests(mox.MoxTestBase):

  def setUp(self):
    mox.MoxTestBase.setUp(self)

    self.patch = cros_patch.LocalPatch('my/project', 'manifest_branch',
                                  '/tmp/patch_dir/1', 'mybranch')
    self.buildroot = '/b'

    self.mox.StubOutWithMock(cros_lib, 'GetProjectDir')
    self.mox.StubOutWithMock(cros_patch, '_GetProjectManifestBranch')
    self.mox.StubOutWithMock(cros_patch.LocalPatch, '_GetFileList')
    self.mox.StubOutWithMock(cros_lib, 'RunCommand')

  def testSuccessRun(self):
    """Test a successful run."""
    cros_patch._GetProjectManifestBranch(self.buildroot,
                                   'my/project').AndReturn('manifest_branch')
    cros_lib.GetProjectDir(mox.IgnoreArg(), 'my/project').AndReturn('mydir')
    cros_lib.RunCommand(mox.In('repo') and mox.In('start'), cwd='mydir')
    cros_patch.LocalPatch._GetFileList().AndReturn(['abc.patch', 'bbb.patch'])
    cros_lib.RunCommand(mox.In('git') and mox.In('am') and mox.In('abc.patch'),
                        cwd='mydir')
    self.mox.ReplayAll()

    self.patch.Apply(self.buildroot)
    self.mox.VerifyAll()

  def testWrongTrackingBranch(self):
    """When the original patch branch does not track buildroot's branch."""
    cros_patch._GetProjectManifestBranch(self.buildroot,
                                   'my/project').AndReturn('different_branch')
    self.mox.ReplayAll()

    self.assertRaises(cros_patch.PatchException, self.patch.Apply,
                      self.buildroot)

    self.mox.VerifyAll()


class HelperFunctionTests(mox.MoxTestBase):

  def setUp(self):
    mox.MoxTestBase.setUp(self)

  def testRemovePatchRoot(self):
    """Test successful patch directory removal case."""
    self.mox.StubOutWithMock(cros_patch.shutil, 'rmtree')
    cros_patch.shutil.rmtree('/path/to/tmp/trybot_patch-1111')
    self.mox.ReplayAll()
    cros_patch.RemovePatchRoot('/path/to/tmp/trybot_patch-1111')

  def testRemovePatchRootFail(self):
    """Test when patch directory does not have trybot prefix."""
    self.mox.StubOutWithMock(cros_patch.shutil, 'rmtree')
    self.mox.ReplayAll()
    self.assertRaises(AssertionError, cros_patch.RemovePatchRoot,
                      '/path/to/tmp/bad_prefix-1111')


if __name__ == '__main__':
  logging_format = '%(asctime)s - %(filename)s - %(levelname)-8s: %(message)s'
  date_format = '%H:%M:%S'
  logging.basicConfig(level=logging.DEBUG, format=logging_format,

                      datefmt=date_format)
  unittest.main()
