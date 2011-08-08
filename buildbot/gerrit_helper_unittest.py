#!/usr/bin/python

# Copyright (c) 2011 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unittests for GerritHelper.  Needs to have mox installed."""

import mox
import sys
import unittest

import constants
sys.path.append(constants.SOURCE_ROOT)
from chromite.buildbot import gerrit_helper
from chromite.lib import cros_build_lib as cros_lib


class GerritHelperTest(mox.MoxTestBase):

  def setUp(self):
    mox.MoxTestBase.setUp(self)
    results = (
        '{"project":"chromiumos/platform/init","branch":"master",'
        '"id":"Iee5c89d929f1850d7d4e1a4ff5f21adda800025e",'
        '"number":"1111",'
        '"subject":"init commit",'
        '"owner":{"name":"Init master","email":"init@chromium.org"},'
        '"currentPatchSet":{"number":"2","ref":"refs/changes/72/5172/1",'
            '"revision":"ff10979dd360e75ff21f5cf53b7f8647578785ef"},'
        '"url":"http://gerrit.chromium.org/gerrit/1111",'
        '"lastUpdated":1311024429,'
        '"sortKey":"00166e8700001051",'
        '"open":true,"'
        'status":"NEW"}'
        '\n'
        '{"project":"tacos/chromite","branch":"master",'
        '"id":"Iee5c89d929f1850d7d4e1a4ff5f21adda800025f",'
        '"currentPatchSet":{"number":"2","ref":"refs/changes/72/5172/1",'
            '"revision":"ff10979dd360e75ff21f5cf53b7f8647578785ef"},'
        '"number":"1112",'
        '"subject":"chromite commit",'
        '"owner":{"name":"Chromite Master","email":"chromite@chromium.org"},'
        '"url":"http://gerrit.chromium.org/gerrit/1112",'
        '"lastUpdated":1311024529,'
        '"sortKey":"00166e8700001052",'
        '"open":true,"'
        'status":"NEW"}'
        )

    self.results = results

  def testParseFakeResults(self):
    """Parses our own fake gerrit query results to verify we parse correctly."""
    fake_result = self.mox.CreateMock(cros_lib.CommandResult)
    fake_result.output = self.results
    self.mox.StubOutWithMock(cros_lib, 'RunCommand')
    cros_lib.RunCommand(mox.In('gerrit.chromium.org'),
                        redirect_stdout=True).AndReturn(fake_result)
    self.mox.ReplayAll()
    helper = gerrit_helper.GerritHelper(False)
    changes = helper.GrabChangesReadyForCommit('master')
    self.assertEqual(len(changes), 2)
    self.assertEqual(changes[0].id, 'Iee5c89d929f1850d7d4e1a4ff5f21adda800025e')
    self.assertEqual(changes[1].id, 'Iee5c89d929f1850d7d4e1a4ff5f21adda800025f')
    self.mox.VerifyAll()

  def testParseFakeResultsWithInternalURL(self):
    """Parses our own fake gerrit query results but sets internal bit."""
    fake_result = self.mox.CreateMock(cros_lib.CommandResult)
    fake_result.output = self.results
    self.mox.StubOutWithMock(cros_lib, 'RunCommand')
    cros_lib.RunCommand(mox.In('gerrit-int.chromium.org'),
                        redirect_stdout=True).AndReturn(fake_result)
    self.mox.ReplayAll()
    helper = gerrit_helper.GerritHelper(True)
    changes = helper.GrabChangesReadyForCommit('master')
    self.assertEqual(len(changes), 2)
    self.assertEqual(changes[0].id, 'Iee5c89d929f1850d7d4e1a4ff5f21adda800025e')
    self.assertEqual(changes[1].id, 'Iee5c89d929f1850d7d4e1a4ff5f21adda800025f')
    self.mox.VerifyAll()

  def _PrintChanges(self, changes):
    """Deep print of an array of changes."""
    for change in changes:
      print change

  def testRealCommandWorks(self):
    """This is just a sanity test that the command is valid.

    Runs the command and prints out the changes.  Should not throw an exception.
    """
    helper = gerrit_helper.GerritHelper(False)
    changes = helper.GrabChangesReadyForCommit('master')
    self._PrintChanges(changes)

  def testInternalCommandWorks(self):
    """This is just a sanity test that the internal command is valid.

    Runs the command and prints out the changes.  Should not throw an exception.
    """
    helper = gerrit_helper.GerritHelper(True)
    changes = helper.GrabChangesReadyForCommit('master')
    self._PrintChanges(changes)

  def testFilterWithOwnManifestFakeResults(self):
    """Runs through a filter of own manifest and fake changes.

    This test should filter out the tacos/chromite project as its not real.
    """
    fake_result_from_gerrit = self.mox.CreateMock(cros_lib.CommandResult)
    fake_result_from_gerrit.output = self.results
    self.mox.StubOutWithMock(cros_lib, 'RunCommand')
    cros_lib.RunCommand(mox.In('gerrit.chromium.org'),
                        redirect_stdout=True).AndReturn(fake_result_from_gerrit)
    self.mox.ReplayAll()
    helper = gerrit_helper.GerritHelper(False)
    changes = helper.GrabChangesReadyForCommit('master')
    new_changes = helper.FilterProjectsNotInSourceTree(changes,
                                                       constants.SOURCE_ROOT)
    self.assertEqual(len(new_changes), 1)
    self.assertFalse(new_changes[0].project == 'tacos/chromite')

  def testFilterWithOwnManifest(self):
    """Runs through a filter of own manifest and current changes."""
    helper = gerrit_helper.GerritHelper(False)
    changes = helper.GrabChangesReadyForCommit('master')
    print 'Changes BEFORE filtering ***'
    self._PrintChanges(changes)
    new_changes = helper.FilterProjectsNotInSourceTree(changes,
                                                       constants.SOURCE_ROOT)
    print 'Changes AFTER filtering ***'
    self._PrintChanges(new_changes)


if __name__ == '__main__':
  unittest.main()
