#!/usr/bin/python

# Copyright (c) 2011 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unittests for commands.  Needs to be run inside of chroot for mox."""

import logging
import mox
import os
import sys
import copy
import tempfile
import unittest

import constants
sys.path.insert(0, constants.SOURCE_ROOT)
from chromite.lib import cros_build_lib as cros_lib
from chromite.buildbot import patch as cros_patch
from chromite.buildbot import gerrit_helper

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


class GerritPatchTest(mox.MoxTestBase):

  @property
  def test_json(self):
    return copy.deepcopy(FAKE_PATCH_JSON)

  def MockCommandResult(self, **kwds):
    obj = self.mox.CreateMock(cros_lib.CommandResult)
    kwds.setdefault('returncode', 0)
    for attr, val in kwds.iteritems():
      setattr(obj, attr, val)
    return obj

  def GerritDependenciesHelper(self, cmd_output, expected_return_tuple):
    build_root = 'fake_build_root'
    project_dir = 'fake_build_root/fake_project_dir'
    self.mox.StubOutWithMock(cros_lib, 'RunCommand')
    self.mox.StubOutWithMock(cros_patch, '_GetProjectManifestBranch')
    self.mox.StubOutWithMock(cros_lib, 'GetProjectDir')

    my_patch = cros_patch.GerritPatch(self.test_json, False)
    cros_lib.GetProjectDir(build_root, 'tacos/chromite').AndReturn(project_dir)
    # Ignore git fetch.
    cros_lib.RunCommand(mox.IgnoreArg(), cwd=project_dir, print_cmd=False)
    cros_patch._GetProjectManifestBranch(
        build_root, 'tacos/chromite').AndReturn('m/master')

    git_log = self.MockCommandResult(output=cmd_output)

    cros_lib.RunCommand(
        ['git', 'log', '-z', 'm/master..FETCH_HEAD^'], cwd=project_dir,
        redirect_stdout=True, print_cmd=False).AndReturn(git_log)

    self.mox.ReplayAll()
    deps = my_patch.GerritDependencies(build_root)
    self.mox.VerifyAll()

    self.assertEqual(deps, expected_return_tuple)

  def PaladinDepenedenciesHelper(self, commit_msg, expected_return_tuple):
    build_root = 'fake_build_root'
    self.mox.StubOutWithMock(cros_patch.GerritPatch, 'CommitMessage')

    my_patch = cros_patch.GerritPatch(self.test_json, False)
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
    self.GerritDependenciesHelper('\0'.join([commit1, commit2]),
                                  ['1234abcd', '1234abce'])

  def testGerritNoDependencies(self):
    """Tests that we return an empty tuple if the commit has no deps."""
    self.GerritDependenciesHelper('', [])

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
    my_patch = cros_patch.GerritPatch(self.test_json, False)
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

  def VerifyPatchInfo(self, patch_info, project, branch, tracking_branch):
    """Check the returned GitRepoPatchInfo against golden values."""
    self.assertEquals(patch_info.project, project)
    self.assertEquals(patch_info.ref, branch)
    self.assertEquals(patch_info.tracking_branch, tracking_branch)

  def testBranchSpecifiedSuccessRun(self):
    """Test success with branch specified by user."""
    output_obj = self.mox.CreateMock(cros_lib.CommandResult)
    output_obj.output= '12345'
    cros_lib.GetProjectDir(mox.IgnoreArg(), 'my/project').AndReturn('mydir')
    cros_lib.RunCommand(mox.In('m/master..mybranch'),
                        redirect_stdout=mox.IgnoreArg(),
                        cwd='mydir').AndReturn(output_obj)
    cros_patch._GetRemoteTrackingBranch('mydir',
                                 'mybranch').AndReturn('tracking_branch')
    self.mox.ReplayAll()

    patch_info = cros_patch.PrepareLocalPatches(self.patches, 'master')
    self.VerifyPatchInfo(patch_info[0], 'my/project', 'mybranch',
                         'tracking_branch')
    self.mox.VerifyAll()

  def testBranchSpecifiedNoChanges(self):
    """Test when no changes on the branch specified by user."""
    output_obj = self.mox.CreateMock(cros_lib.CommandResult)
    output_obj.output=''
    cros_lib.GetProjectDir(mox.IgnoreArg(), 'my/project').AndReturn('mydir')
    cros_lib.RunCommand(mox.In('m/master..mybranch'),
                        redirect_stdout=mox.IgnoreArg(),
                        cwd='mydir').AndReturn(output_obj)
    self.mox.ReplayAll()

    self.assertRaises(
        cros_patch.PatchException,
        cros_patch.PrepareLocalPatches,
        self.patches,
        'master')
    self.mox.VerifyAll()

  def testNoTrackingBranch(self):
    """Test when project branch does not track a remote branch."""
    output_obj = self.mox.CreateMock(cros_lib.CommandResult)
    output_obj.output= '12345'
    cros_lib.GetProjectDir(mox.IgnoreArg(), 'my/project').AndReturn('mydir')
    cros_lib.RunCommand(mox.In('m/master..mybranch'),
                        redirect_stdout=mox.IgnoreArg(),
                        cwd='mydir').AndReturn(output_obj)
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

    self.patch = cros_patch.GitRepoPatch('/path/to/my/project.git',
                                         'my/project', 'mybranch',
                                         'master')
    self.buildroot = '/b'
    self.mox.StubOutWithMock(cros_lib, 'GetProjectDir')
    self.mox.StubOutWithMock(cros_patch, '_GetProjectManifestBranch')
    self.mox.StubOutWithMock(cros_lib, 'RunCommand')

  def testWrongTrackingBranch(self):
    """When the original patch branch does not track buildroot's branch."""
    cros_patch._GetProjectManifestBranch(self.buildroot,
                                   'my/project').AndReturn('different_branch')
    self.mox.ReplayAll()

    self.assertRaises(cros_patch.PatchException, self.patch.Apply,
                      self.buildroot)

    self.mox.VerifyAll()


if __name__ == '__main__':
  logging_format = '%(asctime)s - %(filename)s - %(levelname)-8s: %(message)s'
  date_format = '%H:%M:%S'
  logging.basicConfig(level=logging.DEBUG, format=logging_format,

                      datefmt=date_format)
  unittest.main()
