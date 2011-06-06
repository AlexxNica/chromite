#!/usr/bin/python

# Copyright (c) 2011 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unittests for lkgm_manager. Needs to be run inside of chroot for mox."""

import mox
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

if __name__ == '__main__':
  import constants
  sys.path.append(constants.SOURCE_ROOT)

from chromite.buildbot import lkgm_manager
from chromite.buildbot import manifest_version
from chromite.buildbot import manifest_version_unittest


FAKE_VERSION_STRING = '1.2.3.4-rc3'
FAKE_VERSION_STRING_NEXT = '1.2.3.4-rc4'


class LKGMCandidateInfoTest(mox.MoxTestBase):
  """Test methods testing methods in _LKGMCandidateInfo class."""

  def setUp(self):
    mox.MoxTestBase.setUp(self)
    self.tmpdir = tempfile.mkdtemp()

  def testLoadFromString(self):
    """Tests whether we can load from a string."""
    info = lkgm_manager._LKGMCandidateInfo(version_string=FAKE_VERSION_STRING)
    self.assertEqual(info.VersionString(), FAKE_VERSION_STRING)

  def testIncrementVersionPatch(self):
    """Tests whether we can increment a lkgm info."""
    info = lkgm_manager._LKGMCandidateInfo(version_string=FAKE_VERSION_STRING)
    info.IncrementVersion()
    self.assertEqual(info.VersionString(), FAKE_VERSION_STRING_NEXT)

  def testVersionCompare(self):
    """Tests whether our comparision method works."""
    info1 = lkgm_manager._LKGMCandidateInfo('1.2.3.4-rc1')
    info2 = lkgm_manager._LKGMCandidateInfo('1.2.3.4-rc2')
    info3 = lkgm_manager._LKGMCandidateInfo('1.2.200.4-rc1')
    info4 = lkgm_manager._LKGMCandidateInfo('1.4.3.4-rc1')

    self.assertTrue(info2 > info1)
    self.assertTrue(info3 > info1)
    self.assertTrue(info3 > info2)
    self.assertTrue(info4 > info1)
    self.assertTrue(info4 > info2)
    self.assertTrue(info4 > info3)

  def tearDown(self):
    shutil.rmtree(self.tmpdir)


class LKGMManagerTest(mox.MoxTestBase):
  """Tests for the BuildSpecs manager."""

  def setUp(self):
    mox.MoxTestBase.setUp(self)

    self.tmpdir = tempfile.mkdtemp()
    self.source_repo = 'ssh://source/repo'
    self.manifest_repo = 'ssh://manifest/repo'
    self.version_file = 'version-file.sh'
    self.branch = 'master'
    self.build_name = 'x86-generic'
    self.incr_type = 'patch'

    # Change default to something we clean up.
    self.tmpmandir = tempfile.mkdtemp()
    lkgm_manager.LKGMManager._TMP_MANIFEST_DIR = self.tmpmandir

    self.manager = lkgm_manager.LKGMManager(
      self.tmpdir, self.source_repo, self.manifest_repo, self.branch,
      self.build_name, dry_run=True)

    self.manager.all_specs_dir = '/LKGM/path'

    self.manager.SLEEP_TIMEOUT = 1

  def _CommonTestLatestCandidateByVersion(self, version, expected_candidate,
                                          no_all=False):
    """Common helper function for latest candidate tests.

    Helper function to test given a version whether we can the right candidate
    back.

    Args:
      version: The Chrome OS version to look for the latest candidate of.
      expected_candidate: What we expect to come back.
    """
    if not no_all:
      self.manager.all = ['1.2.3.4-rc1',
                          '1.2.3.4-rc2',
                          '1.2.3.4-rc9',
                          '1.2.3.5-rc1',
                          '1.2.3.6-rc2',
                          '1.2.4.3-rc1',
                          ]

    info_for_test = manifest_version.VersionInfo(version)
    candidate = self.manager._GetLatestCandidateByVersion(info_for_test)
    self.assertEqual(candidate.VersionString(), expected_candidate)

  def testGetLatestCandidateByVersionCommonCase(self):
    """Tests whether we can get the latest candidate under the common case.

    This test tests whether or not we get the right candidate when we have
    many of the same candidate version around but with different rc's.
    """
    self._CommonTestLatestCandidateByVersion('1.2.3.4', '1.2.3.4-rc9')

  def testGetLatestCandidateByVersionOnlyOne(self):
    """Tests whether we can get the latest candidate with only one rc."""
    self._CommonTestLatestCandidateByVersion('1.2.3.6', '1.2.3.6-rc2')

  def testGetLatestCandidateByVersionNone(self):
    """Tests whether we can get the latest candidate with no rc's."""
    self._CommonTestLatestCandidateByVersion('1.2.5.7', '1.2.5.7-rc1')

  def testGetLatestCandidateByVersionNoneNoAll(self):
    """Tests whether we can get the latest candidate with no rc's at all."""
    self._CommonTestLatestCandidateByVersion('10.0.1.5', '10.0.1.5-rc1', True)

  def _GetPathToManifest(self, info):
    return os.path.join(self.manager.all_specs_dir, '%s.xml' %
                        info.VersionString())

  def testCreateNewCandidate(self):
    """Tests that we can create a new candidate and uprev and old rc."""
    # Let's stub out other LGKMManager calls cause they're already
    # unit tested.
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager, '_GetCurrentVersionInfo')
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager, '_LoadSpecs')
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager,
                             '_GetLatestCandidateByVersion')
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager, '_CreateNewBuildSpec')
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager, '_SetInFlight')

    my_info = manifest_version.VersionInfo('1.2.3.4')
    most_recent_candidate = lkgm_manager._LKGMCandidateInfo('1.2.3.4-rc12')
    new_candidate = lkgm_manager._LKGMCandidateInfo('1.2.3.4-rc13')

    lkgm_manager.LKGMManager._GetCurrentVersionInfo(
        self.version_file).AndReturn(my_info)
    lkgm_manager.LKGMManager._LoadSpecs(my_info)
    lkgm_manager.LKGMManager._GetLatestCandidateByVersion(my_info).AndReturn(
        most_recent_candidate)
    lkgm_manager.LKGMManager._CreateNewBuildSpec(
        most_recent_candidate).AndReturn(new_candidate.VersionString())
    lkgm_manager.LKGMManager._SetInFlight(
        mox.StrContains(new_candidate.VersionString()))

    self.mox.ReplayAll()
    candidate_path = self.manager.CreateNewCandidate(self.version_file)
    self.assertEqual(candidate_path, self._GetPathToManifest(new_candidate))
    self.mox.VerifyAll()

  def testCreateNewCandidateReturnNoneIfNoWorkToDo(self):
    """Tests that we return nothing if there is nothing to create."""
    # Let's stub out other LGKMManager calls cause they're already
    # unit tested.
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager, '_GetCurrentVersionInfo')
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager, '_LoadSpecs')
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager,
                             '_GetLatestCandidateByVersion')
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager, '_CreateNewBuildSpec')
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager, '_SetInFlight')

    my_info = manifest_version.VersionInfo('1.2.3.4')
    most_recent_candidate = lkgm_manager._LKGMCandidateInfo('1.2.3.4-rc12')

    lkgm_manager.LKGMManager._GetCurrentVersionInfo(
        self.version_file).AndReturn(my_info)
    lkgm_manager.LKGMManager._LoadSpecs(my_info)
    lkgm_manager.LKGMManager._GetLatestCandidateByVersion(my_info).AndReturn(
        most_recent_candidate)
    lkgm_manager.LKGMManager._CreateNewBuildSpec(
        most_recent_candidate).AndReturn(None)

    self.mox.ReplayAll()
    candidate = self.manager.CreateNewCandidate(self.version_file)
    self.assertEqual(candidate, None)
    self.mox.VerifyAll()

  def testGetLatestCandidate(self):
    """Makes sure we can get the latest created candidate manifest."""
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager, '_GetCurrentVersionInfo')
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager, '_LoadSpecs')
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager, '_SetInFlight')

    my_info = manifest_version.VersionInfo('1.2.3.4')
    most_recent_candidate = lkgm_manager._LKGMCandidateInfo('1.2.3.4-rc12')

    lkgm_manager.LKGMManager._GetCurrentVersionInfo(
        self.version_file).AndReturn(my_info)
    lkgm_manager.LKGMManager._LoadSpecs(my_info)
    lkgm_manager.LKGMManager._SetInFlight(
        mox.StrContains(most_recent_candidate.VersionString()))

    self.mox.ReplayAll()
    self.manager.latest_unprocessed = '1.2.3.4-rc12'
    candidate = self.manager.GetLatestCandidate(self.version_file)
    self.assertEqual(candidate, self._GetPathToManifest(most_recent_candidate))
    self.mox.VerifyAll()

  def testGetLatestCandidateNone(self):
    """Makes sure we get nothing if there is no work to be done."""
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager, '_GetCurrentVersionInfo')
    self.mox.StubOutWithMock(lkgm_manager.LKGMManager, '_LoadSpecs')

    my_info = manifest_version.VersionInfo('1.2.3.4')

    lkgm_manager.LKGMManager._GetCurrentVersionInfo(
        self.version_file).AndReturn(my_info)
    lkgm_manager.LKGMManager._LoadSpecs(my_info)

    self.mox.ReplayAll()
    candidate = self.manager.GetLatestCandidate(self.version_file)
    self.assertEqual(candidate, None)
    self.mox.VerifyAll()

  def _CreateManifest(self):
    """Returns a created test manifest in tmpdir with its dir_pfx."""
    self.manager.current_version = '1.2.3.4-rc21'
    dir_pfx = '1.2'
    manifest = os.path.join(self.manager.manifests_dir,
                            lkgm_manager.LKGMManager.LGKM_SUBDIR, 'buildspecs',
                            dir_pfx, '1.2.3.4-rc21.xml')
    manifest_version_unittest.TouchFile(manifest)
    return manifest, dir_pfx

  @staticmethod
  def _FinishBuilderHelper(manifest, path_for_builder, dir_pfx, status, wait=0):
    time.sleep(wait)
    manifest_version._CreateSymlink(
        manifest, os.path.join(path_for_builder, status, dir_pfx,
                               os.path.basename(manifest)))

  def _FinishBuild(self, manifest, path_for_builder, dir_pfx, status, wait=0):
    """Finishes a build by marking a status with optional delay."""
    if wait > 0:
      thread = threading.Thread(target=self._FinishBuilderHelper,
                                args=(manifest, path_for_builder, dir_pfx,
                                      status, wait))
      thread.start()
      return thread
    else:
      self._FinishBuilderHelper(manifest, path_for_builder, dir_pfx, status, 0)

  def testGetBuildersStatusBothFinished(self):
    """Tests GetBuilderStatus where both builds have finished."""
    self.mox.StubOutWithMock(lkgm_manager, '_SyncGitRepo')

    manifest, dir_pfx = self._CreateManifest()
    for_build1 = os.path.join(self.manager.manifests_dir,
                              lkgm_manager.LKGMManager.LGKM_SUBDIR,
                              'build-name', 'build1')
    for_build2 = os.path.join(self.manager.manifests_dir,
                              lkgm_manager.LKGMManager.LGKM_SUBDIR,
                              'build-name', 'build2')

    self._FinishBuild(manifest, for_build1, dir_pfx, 'fail')
    self._FinishBuild(manifest, for_build2, dir_pfx, 'pass')

    lkgm_manager._SyncGitRepo(self.manager.manifests_dir)
    self.mox.ReplayAll()
    statuses = self.manager.GetBuildersStatus(['build1', 'build2'])
    self.assertEqual(statuses['build1'], 'fail')
    self.assertEqual(statuses['build2'], 'pass')
    self.mox.VerifyAll()

  def testGetBuildersStatusWaitForOne(self):
    """Tests GetBuilderStatus where both builds have finished with one delay."""
    self.mox.StubOutWithMock(lkgm_manager, '_SyncGitRepo')

    manifest, dir_pfx = self._CreateManifest()
    for_build1 = os.path.join(self.manager.manifests_dir,
                              lkgm_manager.LKGMManager.LGKM_SUBDIR,
                              'build-name', 'build1')
    for_build2 = os.path.join(self.manager.manifests_dir,
                              lkgm_manager.LKGMManager.LGKM_SUBDIR,
                              'build-name', 'build2')

    self._FinishBuild(manifest, for_build1, dir_pfx, 'fail')
    self._FinishBuild(manifest, for_build2, dir_pfx, 'pass', wait=3)

    lkgm_manager._SyncGitRepo(self.manager.manifests_dir).MultipleTimes()
    self.mox.ReplayAll()

    statuses = self.manager.GetBuildersStatus(['build1', 'build2'])
    self.assertEqual(statuses['build1'], 'fail')
    self.assertEqual(statuses['build2'], 'pass')
    self.mox.VerifyAll()

  def testGetBuildersStatusReachTimeout(self):
    """Tests GetBuilderStatus where one build finishes and one never does."""
    self.mox.StubOutWithMock(lkgm_manager, '_SyncGitRepo')

    manifest, dir_pfx = self._CreateManifest()
    for_build1 = os.path.join(self.manager.manifests_dir,
                              lkgm_manager.LKGMManager.LGKM_SUBDIR,
                              'build-name', 'build1')
    for_build2 = os.path.join(self.manager.manifests_dir,
                              lkgm_manager.LKGMManager.LGKM_SUBDIR,
                              'build-name', 'build2')

    self._FinishBuild(manifest, for_build1, dir_pfx, 'fail', wait=3)
    thread = self._FinishBuild(manifest, for_build2, dir_pfx, 'pass', wait=5)

    lkgm_manager._SyncGitRepo(self.manager.manifests_dir).MultipleTimes()
    self.mox.ReplayAll()
    # Let's reduce this.
    self.manager.MAX_TIMEOUT_SECONDS = 5
    statuses = self.manager.GetBuildersStatus(['build1', 'build2'])
    self.assertEqual(statuses['build1'], 'fail')
    self.assertEqual(statuses['build2'], None)
    thread.join()
    self.mox.VerifyAll()

  def tearDown(self):
    if os.path.exists(self.tmpdir): shutil.rmtree(self.tmpdir)
    shutil.rmtree(self.tmpmandir)


if __name__ == '__main__':
  unittest.main()
