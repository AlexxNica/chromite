#!/usr/bin/python

# Copyright (c) 2011-2012 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Module that contains unittests for validation_pool module."""

import contextlib
import copy
import functools
import itertools
import mox
import os
import pickle
import sys
import time
import unittest
import urllib

import constants
sys.path.insert(0, constants.SOURCE_ROOT)

from chromite.buildbot import cbuildbot_results as results_lib
from chromite.buildbot import gerrit_helper
from chromite.buildbot import patch as cros_patch
from chromite.buildbot import patch_unittest
from chromite.buildbot import repository
from chromite.buildbot import validation_pool
from chromite.lib import cros_build_lib
from chromite.lib import cros_test_lib

_GetNumber = iter(itertools.count()).next

class MockPatch(mox.MockObject):

  owner = 'elmer.fudd@google.com'

  def __eq__(self, other):
    return self.id == getattr(other, 'id')

def GetTestJson(change_id=None):
  """Get usable fake Gerrit patch json data

  Args:
    change_id: If given, force this ChangeId
  """
  data = copy.deepcopy(patch_unittest.FAKE_PATCH_JSON)
  if change_id is not None:
    data['id'] = str(change_id)
  return data


class MockManifest(object):

  def __init__(self, path, **kwds):
    self.root = path
    for key, attr in kwds.iteritems():
      setattr(self, key, attr)

  def GetProjectPath(self, project, absolute=False):
    if absolute:
      return os.path.join(self.root, project)
    return project

  def GetProjectsLocalRevision(self, _project):
    return 'refs/remotes/cros/master'


# pylint: disable=W0212,R0904
class base_mixin(object):

  def setUp(self):
    mox.MoxTestBase.setUp(self)
    self.mox.StubOutWithMock(validation_pool, '_RunCommand')
    self.mox.StubOutWithMock(time, 'sleep')
    self.mox.StubOutWithMock(validation_pool.ValidationPool, '_IsTreeOpen')
    # Supress all gerrit access; having this occur is generally a sign
    # the code is either misbehaving, or the tests are bad.
    self.mox.StubOutWithMock(gerrit_helper.GerritHelper, 'Query')
    self.mox.StubOutWithMock(gerrit_helper.GerritHelper, '_SqlQuery')
    self._patch_counter = (itertools.count(1)).next
    self.build_root = 'fakebuildroot'

  def MockPatch(self, change_id=None, patch_number=None, is_merged=False,
                project='chromiumos/chromite', internal=False,
                tracking_branch='refs/heads/master'):
    # pylint: disable=W0201
    # We have to use a custom mock class to fix some brain behaviour of
    # pymox where multiple separate mocks can easily equal each other
    # (or not; the behaviour varies depending on stubs used).
    patch = MockPatch(cros_patch.GerritPatch)
    self.mox._mock_objects.append(patch)

    patch.internal = internal
    if change_id is None:
      change_id = self._patch_counter()
    patch.gerrit_number = str(change_id)
    # Strip off the leading 0x, trailing 'l'
    change_id = hex(change_id)[2:].rstrip('L').lower()
    patch.change_id = patch.id = 'I%s' % change_id.rjust(40, '0')
    patch.patch_number = (patch_number if patch_number is not None else
                          _GetNumber())
    patch.url = 'fake_url/%s' % (change_id,)
    patch.project = project
    patch.sha1 = hex(_GetNumber())[2:].rstrip('L').lower().rjust(40, '0')
    patch.IsAlreadyMerged = lambda:is_merged
    patch.LookupAliases = functools.partial(
        self._LookupAliases, patch)
    patch.tracking_branch = tracking_branch
    return patch

  @staticmethod
  def _LookupAliases(patch):
    return [patch.change_id, patch.sha1, patch.gerrit_number]

  def GetPatches(self, how_many=1, **kwargs):
    l = [self.MockPatch(**kwargs) for _ in xrange(how_many)]
    if how_many == 1:
      return l[0]
    return l

  def MakeHelper(self, internal=None, external=None):
    if internal:
      internal = self.mox.CreateMock(gerrit_helper.GerritHelper)
      internal.version = '2.1'
      internal.internal = True
    if external:
      external = self.mox.CreateMock(gerrit_helper.GerritHelper)
      external.internal = False
      external.version = '2.1'
    return validation_pool.HelperPool(internal=internal, external=external)


# pylint: disable=W0212,R0904
class TestPatchSeries(base_mixin, mox.MoxTestBase):
  """Tests the core resolution and applying logic of
  validation_pool.ValidationPool."""

  def setUp(self):
    base_mixin.setUp(self)
    # All tests should set their content merging projects via
    # SetContentMergingProjects since FindContentMergingProjects
    # requires admin rights in gerrit.
    self.mox.StubOutWithMock(gerrit_helper.GerritHelper,
                             'FindContentMergingProjects')

  @staticmethod
  def SetContentMergingProjects(series, projects=(), internal=False):
    helper = series._helper_pool.GetHelper(internal)
    series._content_merging_projects[helper] = frozenset(projects)

  @contextlib.contextmanager
  def _ValidateTransactionCall(self, _changes):
    yield

  def GetPatchSeries(self, helper_pool=None, force_content_merging=False):
    if helper_pool is None:
      helper_pool = self.MakeHelper(internal=True, external=True)
    series = validation_pool.PatchSeries(self.build_root, helper_pool,
                                         force_content_merging)

    # Suppress transactions.
    series._Transaction = self._ValidateTransactionCall

    return series

  def assertPath(self, _patch, return_value, path):
    self.assertEqual(path,
                     os.path.join(self.build_root, _patch.project))
    if isinstance(return_value, Exception):
      raise return_value
    return return_value

  def assertGerritDependencies(self, _patch, return_value, path,
                               tracking):
    self.assertEqual(tracking, 'refs/remotes/cros/master')
    return self.assertPath(_patch, return_value, path)

  def SetPatchDeps(self, patch, parents=(), cq=()):
    patch.GerritDependencies = functools.partial(
        self.assertGerritDependencies, patch, parents)
    patch.PaladinDependencies = functools.partial(
        self.assertPath, patch, cq)
    patch.Fetch = functools.partial(
        self.assertPath, patch, patch.sha1)

  def _ValidatePatchApplyManifest(self, value):
    self.assertTrue(isinstance(value, MockManifest))
    self.assertEqual(value.root, self.build_root)
    return True

  def SetPatchApply(self, patch, trivial=True):
    return patch.ApplyAgainstManifest(
        mox.Func(self._ValidatePatchApplyManifest),
        trivial=trivial)

  def assertResults(self, series, changes, applied=(), failed_tot=(),
                    failed_inflight=(), frozen=True, dryrun=False):
    # Convenience; set the content pool as necessary.
    for internal in set(x.internal for x in changes):
      helper = series._helper_pool.GetHelper(internal)
      series._content_merging_projects.setdefault(helper, frozenset())

    manifest = MockManifest(self.build_root)
    result = series.Apply(changes, dryrun=dryrun,
                          frozen=frozen, manifest=manifest)

    _GetIds = lambda seq:[x.id for x in seq]
    _GetFailedIds = lambda seq:_GetIds(x.patch for x in seq)

    applied_result = _GetIds(result[0])
    failed_tot_result, failed_inflight_result = map(_GetFailedIds, result[1:])

    applied = _GetIds(applied)
    failed_tot = _GetIds(failed_tot)
    failed_inflight = _GetIds(failed_inflight)

    self.assertEqual(
        [applied, failed_tot, failed_inflight],
        [applied_result, failed_tot_result, failed_inflight_result])

  def testApplyWithDeps(self):
    """Test that we can apply changes correctly and respect deps.

    This tests a simple out-of-order change where change1 depends on change2
    but tries to get applied before change2.  What should happen is that
    we should notice change2 is a dep of change1 and apply it first.
    """
    series = self.GetPatchSeries()

    patch1, patch2 = patches = self.GetPatches(2)

    self.SetPatchDeps(patch2)
    self.SetPatchDeps(patch1, [patch2.id])

    self.SetPatchApply(patch2)
    self.SetPatchApply(patch1)

    self.mox.ReplayAll()
    self.assertResults(series, patches, [patch2, patch1])
    self.mox.VerifyAll()

  def testSha1Deps(self):
    """Test that we can apply changes correctly and respect sha1 deps.

    This tests a simple out-of-order change where change1 depends on change2
    but tries to get applied before change2.  What should happen is that
    we should notice change2 is a dep of change1 and apply it first.
    """
    series = self.GetPatchSeries()

    patch1, patch2, patch3 = patches = self.GetPatches(3)
    patch2.change_id = patch2.id = patch2.sha1
    patch3.change_id = patch3.id = '*' + patch3.sha1
    patch3.internal = True

    self.SetPatchDeps(patch1, [patch2.sha1])
    self.SetPatchDeps(patch2, ['*%s' % patch3.sha1])
    self.SetPatchDeps(patch3)

    self.SetPatchApply(patch2)
    self.SetPatchApply(patch3)
    self.SetPatchApply(patch1)

    self.mox.ReplayAll()
    self.assertResults(series, patches, [patch3, patch2, patch1])
    self.mox.VerifyAll()

  def testGerritNumberDeps(self):
    """Test that we can apply changes correctly and respect gerrit number deps.

    This tests a simple out-of-order change where change1 depends on change2
    but tries to get applied before change2.  What should happen is that
    we should notice change2 is a dep of change1 and apply it first.
    """
    series = self.GetPatchSeries()

    patch1, patch2, patch3 = patches = self.GetPatches(3)

    self.SetPatchDeps(patch3, cq=[patch1.gerrit_number])
    self.SetPatchDeps(patch2, cq=[patch3.gerrit_number])
    self.SetPatchDeps(patch1, cq=[patch2.id])

    self.SetPatchApply(patch3)
    self.SetPatchApply(patch2)
    self.SetPatchApply(patch1)

    self.mox.ReplayAll()
    self.assertResults(series, patches, [patch1, patch2, patch3])
    self.mox.VerifyAll()

  def testCrosGerritDeps(self):
    """Test that we can apply changes correctly and respect deps.

    This tests a simple out-of-order change where change1 depends on change2
    but tries to get applied before change2.  What should happen is that
    we should notice change2 is a dep of change1 and apply it first.
    """
    series = self.GetPatchSeries()

    patch1 = self.MockPatch(internal=False)
    patch2 = self.MockPatch(internal=True)
    patch3 = self.MockPatch(internal=False)
    patches = [patch3, patch2, patch1]

    self.SetPatchDeps(patch1)
    self.SetPatchDeps(patch2, cq=[patch1.id])
    self.SetPatchDeps(patch3, cq=[patch2.id])

    self.SetPatchApply(patch1)
    self.SetPatchApply(patch2)
    self.SetPatchApply(patch3)

    self.mox.ReplayAll()
    self.assertResults(series, patches, patches)
    self.mox.VerifyAll()

  @staticmethod
  def _SetQuery(series, change):
    helper = series._helper_pool.GetHelper(change.internal)
    return helper.QuerySingleRecord(change.id, must_match=True)

  def testApplyMissingDep(self):
    """Test that we don't try to apply a change without met dependencies.

    Patch2 is in the validation pool that depends on Patch1 (which is not)
    Nothing should get applied.
    """
    series = self.GetPatchSeries()

    patch1, patch2 = self.GetPatches(2)

    self.SetPatchDeps(patch2, [patch1.id])
    self._SetQuery(series, patch1).AndReturn(patch1)

    self.mox.ReplayAll()
    self.assertResults(series, [patch2],
                       [], [patch2])
    self.mox.VerifyAll()

  def testApplyWithCommittedDeps(self):
    """Test that we apply a change with dependency already committed."""
    series = self.GetPatchSeries()

    patch1 = self.MockPatch(1, is_merged=True)
    patch2 = self.MockPatch(2)

    self.SetPatchDeps(patch2, [patch1.id])
    self._SetQuery(series, patch1).AndReturn(patch1)

    self.SetPatchApply(patch2)

    self.mox.ReplayAll()
    self.assertResults(series, [patch2], [patch2])
    self.mox.VerifyAll()

  def testApplyPartialFailures(self):
    """Test that can apply changes correctly when one change fails to apply.

    This tests a simple change order where 1 depends on 2 and 1 fails to apply.
    Only 1 should get tried as 2 will abort once it sees that 1 can't be
    applied.  3 with no dependencies should go through fine.

    Since patch1 fails to apply, we should also get a call to handle the
    failure.
    """
    series = self.GetPatchSeries()

    patch1, patch2, patch3, patch4 = patches = self.GetPatches(4)

    self.SetPatchDeps(patch1)
    self.SetPatchDeps(patch2, [patch1.id])
    self.SetPatchDeps(patch3)
    self.SetPatchDeps(patch4)

    self.SetPatchApply(patch1).AndRaise(
        cros_patch.ApplyPatchException(patch1))

    self.SetPatchApply(patch3)
    self.SetPatchApply(patch4).AndRaise(
        cros_patch.ApplyPatchException(patch1, inflight=True))

    self.mox.ReplayAll()
    self.assertResults(series, patches,
                       [patch3], [patch2, patch1], [patch4])
    self.mox.VerifyAll()

  def testApplyMissingChangeId(self):
    """Test that applies changes correctly with a dep with missing changeid."""
    series = self.GetPatchSeries()

    patch1, patch2 = patches = self.GetPatches(2)

    git_repo = os.path.join(self.build_root, patch1.project)
    patch1.Fetch(git_repo)
    patch1.GerritDependencies(
        git_repo,
        'refs/remotes/cros/master').AndRaise(
            cros_patch.BrokenChangeID(patch1, 'Could not find changeid'))

    self.SetPatchDeps(patch2)
    self.SetPatchApply(patch2)

    self.mox.ReplayAll()
    self.assertResults(series, patches, [patch2], [patch1], [])
    self.mox.VerifyAll()

  def testComplexApply(self):
    """More complex deps test.

    This tests a total of 2 change chains where the first change we see
    only has a partial chain with the 3rd change having the whole chain i.e.
    1->2, 3->1->2, 4->nothing.  Since we get these in the order 1,2,3,4 the
    order we should apply is 2,1,3,4.

    This test also checks the patch order to verify that Apply re-orders
    correctly based on the chain.
    """
    series = self.GetPatchSeries()

    patch1, patch2, patch3, patch4, patch5 = patches = self.GetPatches(5)

    self.SetPatchDeps(patch1, [patch2.id])
    self.SetPatchDeps(patch2)
    self.SetPatchDeps(patch3, [patch1.id, patch2.id])
    self.SetPatchDeps(patch4, cq=[patch5.id])
    self.SetPatchDeps(patch5)

    for patch in (patch2, patch1, patch3, patch4, patch5):
      self.SetPatchApply(patch)

    self.mox.ReplayAll()
    self.assertResults(
        series, patches, [patch2, patch1, patch3, patch4, patch5])
    self.mox.VerifyAll()

  def testApplyStandalonePatches(self):
    """Simple apply of two changes with no dependent CL's."""
    series = self.GetPatchSeries()

    patches = self.GetPatches(3)

    for patch in patches:
      self.SetPatchDeps(patch)

    for patch in patches:
      self.SetPatchApply(patch)

    self.mox.ReplayAll()
    self.assertResults(series, patches, patches)
    self.mox.VerifyAll()


# pylint: disable=W0212,R0904
class TestCoreLogic(base_mixin, mox.MoxTestBase):
  """Tests the core resolution and applying logic of
  validation_pool.ValidationPool."""

  def setUp(self):
    base_mixin.setUp(self)
    self.mox.StubOutWithMock(gerrit_helper.GerritHelper,
                             'FindContentMergingProjects')

  def MakePool(self, overlays=constants.PUBLIC_OVERLAYS, build_number=1,
               builder_name='foon', is_master=True, dryrun=True, **kwds):
    handlers = kwds.pop('handlers', False)
    kwds.setdefault('helper_pool', validation_pool.HelperPool.SimpleCreate())
    kwds.setdefault('changes', [])

    pool = validation_pool.ValidationPool(
        overlays, self.build_root, build_number, builder_name, is_master,
        dryrun, **kwds)
    self.mox.StubOutWithMock(pool, '_SendNotification')
    if handlers:
      self.mox.StubOutWithMock(pool, '_HandleApplySuccess')
      self.mox.StubOutWithMock(pool, '_HandleApplyFailure')
      self.mox.StubOutWithMock(pool, '_HandleCouldNotApply')
    self.mox.StubOutWithMock(pool, '_patch_series')
    return pool

  def MakeFailure(self, patch, inflight=True):
    return cros_patch.ApplyPatchException(patch, inflight=inflight)

  def GetPool(self, changes, applied=(), tot=(),
              inflight=(), dryrun=True, **kwds):
    pool = self.MakePool(changes=changes, **kwds)
    applied = list(applied)
    tot = [self.MakeFailure(x, inflight=False) for x in tot]
    inflight = [self.MakeFailure(x, inflight=True) for x in inflight]
    pool._patch_series.Apply(
        changes, dryrun=dryrun, manifest=mox.IgnoreArg()
        ).AndReturn((applied, tot, inflight))

    for patch in applied:
      pool._HandleApplySuccess(patch).AndReturn(None)

    if tot:
      pool._HandleApplyFailure(tot).AndReturn(None)

    # We stash this on the pool object so we can reuse it during validation.
    # We could stash this in the test instances, but that would break
    # for any tests that do multiple pool instances.

    pool._test_data = (changes, applied, tot, inflight)

    return pool

  def runApply(self, pool, result):
    self.assertEqual(result, pool.ApplyPoolIntoRepo())
    self.assertEqual(pool.changes, pool._test_data[1])
    failed_inflight = pool.changes_that_failed_to_apply_earlier
    expected_inflight = set(pool._test_data[3])
    # Intersect the results, since it's possible there were results failed
    # results that weren't related to the ApplyPoolIntoRepo call.
    self.assertEqual(set(failed_inflight).intersection(expected_inflight),
                     expected_inflight)

    self.assertEqual(pool.changes, pool._test_data[1])

  def testPatchSeriesInteraction(self):
    """Verify the interaction between PatchSeries and ValidationPool.

    Effectively, this validates data going into PatchSeries, and coming back
    out; verifies the hand off to _Handle* functions, but no deeper.
    """
    patches = self.GetPatches(3)

    apply_pool = self.GetPool(patches, applied=patches, handlers=True)
    all_inflight = self.GetPool(patches, inflight=patches, handlers=True)
    all_tot = self.GetPool(patches, tot=patches, handlers=True)
    mixed = self.GetPool(patches, tot=patches[0:1], inflight=patches[1:2],
                         applied=patches[2:3], handlers=True)

    self.mox.ReplayAll()
    self.runApply(apply_pool, True)
    self.runApply(all_inflight, False)
    self.runApply(all_tot, False)
    self.runApply(mixed, True)
    self.mox.VerifyAll()

  def testHandleApplySuccess(self):
    """Validate steps taken for successfull application."""
    patch = self.GetPatches(1)
    pool = self.MakePool()
    pool._SendNotification(patch, mox.StrContains('has picked up your change'))
    self.mox.ReplayAll()
    pool._HandleApplySuccess(patch)
    self.mox.VerifyAll()

  def testHandleApplyFailure(self):
    failures = [cros_patch.ApplyPatchException(x) for x in self.GetPatches(4)]

    notified_patches = failures[:2]
    unnotified_patches = failures[2:]
    master_pool = self.MakePool(dryrun=False)
    slave_pool = self.MakePool(is_master=False)

    self.mox.StubOutWithMock(gerrit_helper.GerritHelper, 'RemoveCommitReady')

    for failure in notified_patches:
      master_pool._SendNotification(
          failure.patch,
          mox.StrContains('failed to apply your change'),
          failure=mox.IgnoreArg())
      # This pylint suppressin shouldn't be necessary, but pylint is invalidly
      # thinking that the first arg isn't passed in; we suppress it to suppress
      # the pylnt bug.
      # pylint: disable=E1120
      gerrit_helper.GerritHelper.RemoveCommitReady(failure.patch, dryrun=False)

    self.mox.ReplayAll()
    master_pool._HandleApplyFailure(notified_patches)
    slave_pool._HandleApplyFailure(unnotified_patches)
    self.mox.VerifyAll()

  def testSubmitPoolFailures(self):
    pool = self.MakePool(dryrun=False)
    patch1, patch2, patch3 = patches = self.GetPatches(3)
    failed = self.GetPatches(3)
    pool.changes = patches[:]
    # While we don't do anything w/ these patches, that's
    # intentional; we're verifying that it isn't submitted
    # if there is a failure.
    pool.changes_that_failed_to_apply_earlier = failed[:]

    self.mox.StubOutWithMock(pool, '_SubmitChange')
    self.mox.StubOutWithMock(pool, '_HandleCouldNotSubmit')

    self.mox.StubOutWithMock(gerrit_helper.GerritHelper, 'IsChangeCommitted')

    pool._SubmitChange(patch1).AndReturn(None)
    gerrit_helper.GerritHelper.IsChangeCommitted(
        str(patch1.gerrit_number), False).AndReturn(True)

    pool._SubmitChange(patch2).AndReturn(None)
    gerrit_helper.GerritHelper.IsChangeCommitted(
        str(patch2.gerrit_number), False).InAnyOrder().AndReturn(False)

    pool._HandleCouldNotSubmit(patch2).InAnyOrder()

    pool._SubmitChange(patch3).AndRaise(
        cros_build_lib.RunCommandError('blah', None))
    pool._HandleCouldNotSubmit(patch3).InAnyOrder().AndReturn(None)

    pool._IsTreeOpen().AndReturn(True)

    self.mox.ReplayAll()
    self.assertRaises(validation_pool.FailedToSubmitAllChangesException,
                      pool.SubmitPool)
    self.mox.VerifyAll()

  def testSubmitPool(self):
    pool = self.MakePool(dryrun=False)
    passed = self.GetPatches(3)
    failed = self.GetPatches(3)
    pool.changes = passed
    pool.changes_that_failed_to_apply_earlier = failed[:]

    self.mox.StubOutWithMock(pool, '_SubmitChange')
    self.mox.StubOutWithMock(pool, '_HandleCouldNotSubmit')
    self.mox.StubOutWithMock(pool, '_HandleApplyFailure')

    self.mox.StubOutWithMock(gerrit_helper.GerritHelper, 'IsChangeCommitted')

    for patch in passed:
      pool._SubmitChange(patch).AndReturn(None)
      gerrit_helper.GerritHelper.IsChangeCommitted(
          str(patch.gerrit_number), False).AndReturn(True)

    pool._HandleApplyFailure(failed)

    pool._IsTreeOpen().AndReturn(True)

    self.mox.ReplayAll()
    pool.SubmitPool()
    self.mox.VerifyAll()

  def testSubmitNonManifestChanges(self):
    """Simple test to make sure we can submit non-manifest changes."""
    pool = self.MakePool(dryrun=False)
    patch1, patch2 = passed = self.GetPatches(2)
    pool.non_manifest_changes = passed[:]

    self.mox.StubOutWithMock(pool, '_SubmitChange')
    self.mox.StubOutWithMock(pool, '_HandleCouldNotSubmit')

    self.mox.StubOutWithMock(gerrit_helper.GerritHelper, 'IsChangeCommitted')

    pool._SubmitChange(patch1).AndReturn(None)
    gerrit_helper.GerritHelper.IsChangeCommitted(
        str(patch1.gerrit_number), False).AndReturn(True)

    pool._SubmitChange(patch2).AndReturn(None)
    gerrit_helper.GerritHelper.IsChangeCommitted(
        str(patch2.gerrit_number), False).AndReturn(True)

    pool._IsTreeOpen().AndReturn(True)

    self.mox.ReplayAll()
    pool.SubmitNonManifestChanges()
    self.mox.VerifyAll()

  def testGerritSubmit(self):
    """Tests submission review string looks correct."""
    pool = self.MakePool(dryrun=False)

    patch = self.GetPatches(1)
    # Force int conversion of gerrit_number to ensure the test is sane.
    cmd = ('ssh -p 29418 gerrit.chromium.org gerrit review '
           '--submit %i,%i' % (int(patch.gerrit_number), patch.patch_number))
    validation_pool._RunCommand(cmd.split(), False).AndReturn(None)
    self.mox.ReplayAll()
    pool._SubmitChange(patch)
    self.mox.VerifyAll()

  def testUnhandledExceptions(self):
    """Test that CQ doesn't loop due to unhandled Exceptions."""
    pool = self.MakePool(dryrun=False)
    patches = self.GetPatches(2)
    pool.changes = patches[:]

    class MyException(Exception):
      pass

    self.mox.StubOutWithMock(pool._patch_series, 'Apply')
    # Suppressed because pylint can't tell that we just replaced Apply via mox.
    # pylint: disable=E1101
    pool._patch_series.Apply(
        patches, dryrun=False, manifest=mox.IgnoreArg()).AndRaise(
        MyException)

    def _ValidateExceptioN(changes):
      for patch in changes:
        self.assertTrue(isinstance(patch, validation_pool.InternalCQError),
                        msg="Expected %s to be type InternalCQError, got %r" %
                        (patch, type(patch)))
      self.assertEqual(set(patches),
                       set(x.patch for x in changes))

    self.mox.ReplayAll()
    self.assertRaises(MyException, pool.ApplyPoolIntoRepo)
    self.mox.VerifyAll()

  def testFilterNonCrosProjects(self):
    """Runs through a filter of own manifest and fake changes.

    This test should filter out the tacos/chromite project as its not real.
    """
    base_func = itertools.cycle(['chromiumos', 'chromeos']).next
    patches = self.GetPatches(8)
    for patch in patches:
      patch.project = '%s/%i' % (base_func(), _GetNumber())
      patch.tracking_branch = str(_GetNumber())

    non_cros_patches = self.GetPatches(2)
    for patch in non_cros_patches:
      patch.project = str(_GetNumber())

    filtered_patches = patches[:4]
    allowed_patches = []
    projects = {}
    for idx, patch in enumerate(patches[4:]):
      fails = bool(idx % 2)
      # Vary the revision so we can validate that it checks the branch.
      revision = ('monkeys' if fails
                  else 'refs/heads/%s' % patch.tracking_branch)
      if fails:
        filtered_patches.append(patch)
      else:
        allowed_patches.append(patch)
      projects.setdefault(patch.project, {})['revision'] = revision

    manifest = MockManifest(self.build_root, projects=projects)

    self.mox.ReplayAll()
    results = validation_pool.ValidationPool._FilterNonCrosProjects(
        patches + non_cros_patches, manifest)

    def compare(list1, list2):
      mangle = lambda c:(c.id, c.project, c.tracking_branch)
      self.assertEqual(list1, list2,
        msg="Comparison failed:\n list1: %r\n list2: %r"
            % (map(mangle, list1), map(mangle, list2)))

    compare(results[0], allowed_patches)
    compare(results[1], filtered_patches)


# pylint: disable=W0212,R0904
class TestTreeStatus(mox.MoxTestBase):
  """Tests methods in validation_pool.ValidationPool."""

  def setUp(self):
    mox.MoxTestBase.setUp(self)
    self.mox.StubOutWithMock(validation_pool, '_RunCommand')
    self.mox.StubOutWithMock(time, 'sleep')

  def _TreeStatusFile(self, message, general_state):
    """Returns a file-like object with the status message writtin in it."""
    my_response = self.mox.CreateMockAnything()
    my_response.json = '{"message": "%s", "general_state": "%s"}' % (
        message, general_state)
    return my_response

  @cros_build_lib.TimeoutDecorator(3)
  def _TreeStatusTestHelper(self, tree_status, general_state, expected_return,
                            retries_500=0, max_timeout=0):
    """Tests whether we return the correct value based on tree_status."""
    return_status = self._TreeStatusFile(tree_status, general_state)
    self.mox.StubOutWithMock(urllib, 'urlopen')
    status_url = 'https://chromiumos-status.appspot.com/current?format=json'
    backoff = 1
    for _attempt in range(retries_500):
      urllib.urlopen(status_url).AndReturn(return_status)
      return_status.getcode().AndReturn(500)
      time.sleep(backoff)
      backoff *= 2

    urllib.urlopen(status_url).MultipleTimes().AndReturn(return_status)
    if expected_return == False:
      self.mox.StubOutWithMock(time, 'time')
      time.time().AndReturn(1)
      time.time().AndReturn(1)
      sleep_timeout = min(max(max_timeout / 5, 1), 30)
      x = 0
      while x < max_timeout:
        time.time().AndReturn(x + 1)
        x += sleep_timeout
      time.time().AndReturn(max_timeout + 1)
      time.sleep(mox.IgnoreArg()).MultipleTimes()

    return_status.getcode().MultipleTimes().AndReturn(200)
    return_status.read().MultipleTimes().AndReturn(return_status.json)
    self.mox.ReplayAll()
    self.assertEqual(validation_pool.ValidationPool._IsTreeOpen(max_timeout),
                     expected_return)
    self.mox.VerifyAll()

  def testTreeIsOpen(self):
    """Tests that we return True is the tree is open."""
    self._TreeStatusTestHelper('Tree is open (flaky bug on flaky builder)',
                               'open', True)

  def testTreeIsOpenAlwaysOnBranches(self):
    """Tests that we return True is the tree is open."""
    self.mox.StubOutWithMock(cros_build_lib, 'GetChromiteTrackingBranch')
    cros_build_lib.GetChromiteTrackingBranch().AndReturn('release-ooga-booga')
    self.mox.ReplayAll()
    self.assertTrue(validation_pool.ValidationPool._IsTreeOpen(max_timeout=10))

  def testTreeIsClosed(self):
    """Tests that we return false is the tree is closed."""
    self._TreeStatusTestHelper('Tree is closed (working on a patch)', 'closed',
                               False, max_timeout=5)

  def testTreeIsOpenWithTimeout(self):
    """Tests that we return True even if we get some failures."""
    self._TreeStatusTestHelper('Tree is open (flaky test)', 'open',
                               True, retries_500=2)

  def testTreeIsThrottled(self):
    """Tests that we return false is the tree is throttled."""
    self._TreeStatusTestHelper('Tree is throttled (waiting to cycle)',
                               'throttled', True)

  def testTreeStatusWithNetworkFailures(self):
    """Checks for non-500 errors.."""
    self._TreeStatusTestHelper('Tree is open (flaky bug on flaky builder)',
                               'open', True, retries_500=2)



class TestPickling(cros_test_lib.TempDirMixin, unittest.TestCase):

  """Tests to validate pickling of ValidationPool, covering CQ's needs"""

  def testSelfCompatibility(self):
    """Verify compatibility of current git HEAD against itself."""
    self._CheckTestData(self._GetTestData())

  def testToTCompatibility(self):
    """Validate that ToT can use our pickles, and that we can use ToT's data."""
    repo = os.path.join(self.tempdir, 'chromite')
    reference = os.path.abspath(__file__)
    reference = os.path.normpath(os.path.join(reference, '../../'))

    repository.CloneGitRepo(repo,
                            '%s/chromiumos/chromite' % constants.GIT_HTTP_URL,
                            reference=reference)

    code = """
import sys
from chromite.buildbot import validation_pool_unittest
if not hasattr(validation_pool_unittest, 'TestPickling'):
  sys.exit(0)
sys.stdout.write(validation_pool_unittest.TestPickling.%s)
"""

    # Verify ToT can take our pickle.
    cros_build_lib.RunCommandCaptureOutput(
        ['python', '-c', code % '_CheckTestData(sys.stdin.read())'],
        cwd=self.tempdir, print_cmd=False,
        input=self._GetTestData())

    # Verify we can handle ToT's pickle.
    ret = cros_build_lib.RunCommandCaptureOutput(
        ['python', '-c', code % '_GetTestData()'],
        cwd=self.tempdir, print_cmd=False)

    self._CheckTestData(ret.output)

  @staticmethod
  def _GetTestData():
    ids = [cros_patch.MakeChangeId() for _ in xrange(3)]
    changes = [cros_patch.GerritPatch(GetTestJson(ids[0]), True)]
    non_os = [cros_patch.GerritPatch(GetTestJson(ids[1]), False)]
    conflicting = [cros_patch.GerritPatch(GetTestJson(ids[2]), True)]
    conflicting = [cros_patch.PatchException(x) for x in conflicting]
    pool = validation_pool.ValidationPool(
        constants.PUBLIC_OVERLAYS,
        '/fake/pathway', 1,
        'testing', True, True,
        changes=changes, non_os_changes=non_os,
        conflicting_changes=conflicting)
    return pickle.dumps([pool, changes, non_os, conflicting])

  @staticmethod
  def _CheckTestData(data):
    results = pickle.loads(data)
    pool, changes, non_os, conflicting = results
    def _f(source, value, getter=lambda x:x):
      assert len(source) == len(value)
      for s_item, v_item in zip(source, value):
        assert getter(s_item).id == getter(v_item).id
        assert getter(s_item).internal == getter(v_item).internal
    _f(pool.changes, changes)
    _f(pool.non_manifest_changes, non_os)
    _f(pool.changes_that_failed_to_apply_earlier, conflicting,
       getter=lambda s:getattr(s, 'patch', s))
    return ''


class TestFindSuspects(base_mixin, mox.MoxTestBase):
  """Tests validation_pool.ValidationPool._FindSuspects"""

  def setUp(self):
    base_mixin.setUp(self)
    overlay = 'chromiumos/overlays/chromiumos-overlay'
    self.overlay_patch = self.GetPatches(project=overlay)
    self.power_manager = 'chromiumos/platform/power_manager'
    self.power_manager_pkg = 'chromeos-base/power_manager'
    self.power_manager_patch = self.GetPatches(project=self.power_manager)
    self.kernel = 'chromiumos/third_party/kernel'
    self.kernel_pkg = 'sys-kernel/chromeos-kernel'
    self.kernel_patch = self.GetPatches(project=self.kernel)
    self.secret = 'chromeos/secret'
    self.secret_patch = self.GetPatches(project=self.secret, internal=True)

  @staticmethod
  def _GetBuildFailure(pkg):
    """Create a PackageBuildFailure for the specified |pkg|.

    Args:
      pkg: Package that failed to build.
    """
    ex = cros_build_lib.RunCommandError('foo', cros_build_lib.CommandResult())
    return results_lib.PackageBuildFailure(ex, 'bar', [pkg])

  def _AssertSuspects(self, patches, suspects, pkgs=(), exceptions=(),
                      internal=False):
    """Run _FindSuspects and verify its output.

    Args:
      patches: List of patches to look at.
      suspects: Expected list of suspects returned by _FindSuspects.
      pkgs: List of packages that failed with exceptions in the build.
      exceptions: List of other exceptions that occurred during the build.
      internal: Whether the failures occurred on an internal bot.
    """
    all_exceptions = list(exceptions) + [self._GetBuildFailure(x) for x in pkgs]
    tracebacks = []
    for ex in all_exceptions:
      tracebacks.append(results_lib.RecordedTraceback('Build', ex, str(ex)))
    message = validation_pool.ValidationFailedMessage(
        'foo', 'bar', tracebacks, internal)
    results = validation_pool.ValidationPool._FindSuspects(patches, [message])
    self.assertEquals(set(suspects), results)

  def testFailSameProject(self):
    """Patches to the package that failed should be marked as failing."""
    suspects = [self.kernel_patch]
    patches = suspects + [self.power_manager_patch, self.secret_patch]
    self._AssertSuspects(patches, suspects, [self.kernel_pkg])

  def testFailSameProjectPlusOverlay(self):
    """Patches to the overlay should be marked as failing."""
    suspects = [self.overlay_patch, self.kernel_patch]
    patches = suspects + [self.power_manager_patch, self.secret_patch]
    self._AssertSuspects(patches, suspects, [self.kernel_pkg])

  def testFailUnknownPackage(self):
    """If no patches changed the package, all patches should fail."""
    suspects = [self.overlay_patch, self.power_manager_patch]
    changes = suspects + [self.secret_patch]
    self._AssertSuspects(changes, suspects, [self.kernel_pkg])

  def testFailUnknownException(self):
    """An unknown exception should cause all patches to fail."""
    suspects = [self.kernel_patch, self.power_manager_patch]
    changes = suspects + [self.secret_patch]
    self._AssertSuspects(changes, suspects, exceptions=[Exception('foo bar')])

  def testFailUnknownInternalException(self):
    """An unknown exception should cause all patches to fail."""
    suspects = [self.kernel_patch, self.power_manager_patch, self.secret_patch]
    self._AssertSuspects(suspects, suspects, exceptions=[Exception('foo bar')],
                         internal=True)

  def testFailUnknownCombo(self):
    """An unknown exception should cause all patches to fail, even if there
    are also build failures that we can explain."""
    suspects = [self.kernel_patch, self.power_manager_patch]
    changes = suspects + [self.secret_patch]
    self._AssertSuspects(changes, suspects, [self.kernel_pkg],
                         [Exception('foo bar')])

  def testFailNoExceptions(self):
    """If there are no exceptions, all patches should be failed."""
    suspects = [self.kernel_patch, self.power_manager_patch]
    changes = suspects + [self.secret_patch]
    self._AssertSuspects(changes, suspects)


class SimplePatch(object):

  internal = False

  def __init__(self):
    self.id = _GetNumber()
    self.change_id = "I%s" % str(self.id).rjust(40, "0")

  def __str__(self):
    return str(self.id)


class TestCreateValidationFailureMessage(unittest.TestCase):
  """Tests validation_pool.ValidationPool._CreateValidationFailureMessage"""

  def GetPatches(self, how_many=1):
    patches = [SimplePatch() for _ in xrange(how_many)]
    if how_many == 1:
      return patches[0]
    return patches

  def _AssertMessage(self, change, suspects, messages):
    """Call the _CreateValidationFailureMessage method.

    Args:
      change: The change we are commenting on.
      suspects: List of suspected changes.
      messages: List of messages to include in comment.
    """
    msg = validation_pool.ValidationPool._CreateValidationFailureMessage(
      change, set(suspects), messages)
    for x in messages:
      self.assertTrue(x in msg)
    return msg

  def testSuspectChange(self):
    """Test case where 1 is the only change and is suspect."""
    patch = self.GetPatches(1)
    self._AssertMessage(patch, [patch], ['%s failed' % patch])

  def testInnocentChange(self):
    """Test case where 1 is innocent."""
    patch1, patch2 = self.GetPatches(2)
    self._AssertMessage(patch1, [patch2], ['%s failed' % patch2])

  def testSuspectChanges(self):
    """Test case where 1 is suspected, but so is 2."""
    patches = self.GetPatches(2)
    self._AssertMessage(patches[0], patches,
                        ['%s and %s failed' % tuple(patches)])

  def testInnocentChangeWithMultipleSuspects(self):
    """Test case where 2 and 3 are suspected."""
    patches = self.GetPatches(3)
    self._AssertMessage(patches[0], patches[1:],
                        ['%s and %s failed' % tuple(patches[1:])])

  def testNoSuspects(self):
    """Test case where there are no suspects."""
    self._AssertMessage(self.GetPatches(1), [], ['Internal error'])

  def testNoMessages(self):
    """Test case where there are no messages."""
    patch1 = self.GetPatches(1)
    self._AssertMessage(patch1, [patch1], [])


if __name__ == '__main__':
  cros_build_lib.SetupBasicLogging()
  unittest.main()
