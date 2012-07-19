#!/usr/bin/python

# Copyright (c) 2011-2012 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unittests for commands.  Needs to be run inside of chroot for mox."""

import itertools
import logging
import mox
import os
import sys
import copy
import shutil
import time
import unittest

import constants
sys.path.insert(0, constants.SOURCE_ROOT)
from chromite.lib import cros_build_lib
from chromite.lib import cros_test_lib
from chromite.lib import osutils
from chromite.buildbot import patch as cros_patch
from chromite.buildbot import gerrit_helper

_GetNumber = iter(itertools.count()).next

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

# Change-ID of a known open change in public gerrit.
GERRIT_OPEN_CHANGEID = '8366'
GERRIT_MERGED_CHANGEID = '3'
GERRIT_ABANDONED_CHANGEID = '1'


class TestGitRepoPatch(cros_test_lib.TempDirMixin, unittest.TestCase):

  # No pymox bits are to be used in this class's tests.
  # This needs to actually validate git output, and git behaviour, rather
  # than test our assumptions about git's behaviour/output.

  patch_kls = cros_patch.GitRepoPatch

  COMMIT_TEMPLATE = (
"""commit abcdefgh

Author: Fake person
Date:  Tue Oct 99

I am the first commit.

%(extra)s

%(change-id)s
"""
  )

  # Boolean controlling whether the target class natively knows its
  # ChangeId; only GerritPatches do.
  has_native_change_id = False

  DEFAULT_TRACKING = 'refs/remotes/origin/master'

  def _CreateSourceRepo(self, path):
    """Generate a new repo with a single commit."""
    tmp_path = '%s-tmp' % path
    os.mkdir(path)
    os.mkdir(tmp_path)
    self._run(['git', 'init', '--separate-git-dir', path], cwd=tmp_path)

    # Add an initial commit then wipe the working tree.
    self._run(['git', 'commit', '--allow-empty', '-m', 'initial commit'],
              cwd=tmp_path)
    shutil.rmtree(tmp_path)

  def setUp(self):
    cros_test_lib.TempDirMixin.setUp(self)
    # Create an empty repo to work from.
    self.source = os.path.join(self.tempdir, 'source.git')
    self._CreateSourceRepo(self.source)
    self.default_cwd = os.path.join(self.tempdir, 'unwritable')
    self.original_cwd = os.getcwd()
    os.mkdir(self.default_cwd)
    os.chdir(self.default_cwd)
    # Disallow write so as to smoke out any invalid writes to
    # cwd.
    os.chmod(self.default_cwd, 0500)

  def tearDown(self):
    os.chdir(self.original_cwd)
    # shutil.rmtree won't reset perms on an unwritable directory; do it
    # ourselves.
    os.chmod(self.default_cwd, 0700)
    cros_test_lib.TempDirMixin.tearDown(self)

  def _MkPatch(self, source, sha1, ref='refs/heads/master', **kwds):
    internal = kwds.pop('internal', False)
    return self.patch_kls(source, 'chromiumos/chromite', ref,
                          'origin/master', internal, sha1=sha1, **kwds)

  def _run(self, cmd, cwd=None):
    # Note that cwd is intentionally set to a location the user can't write
    # to; this flushes out any bad usage in the tests that would work by
    # fluke of being invoked from w/in a git repo.
    if cwd is None:
      cwd = self.default_cwd
    return cros_build_lib.RunCommandCaptureOutput(
        cmd, cwd=cwd, print_cmd=False).output.strip()

  def _GetSha1(self, cwd, refspec):
    return self._run(['git', 'rev-list', '-n1', refspec], cwd=cwd)

  def _MakeRepo(self, name, clone, branch='master', alternates=True):
    path = os.path.join(self.tempdir, name)
    cmd = ['git', 'clone', clone, path]
    if alternates:
      cmd += ['--reference', clone]
    self._run(cmd)
    return path

  def _MakeCommit(self, repo, commit=None):
    if commit is None:
      commit = "commit at %s" % (time.time(),)
    self._run(['git', 'commit', '-a', '-m', commit], repo)
    return self._GetSha1(repo, 'HEAD')

  def CommitFile(self, repo, filename, content, commit=None, **kwds):
    osutils.WriteFile(os.path.join(repo, filename), content)
    self._run(['git', 'add', filename], repo)
    sha1 = self._MakeCommit(repo, commit=commit)
    if not self.has_native_change_id:
      kwds.pop('ChangeId', None)
    patch = self._MkPatch(repo, sha1, **kwds)
    self.assertEqual(patch.sha1, sha1)
    return patch

  def _CommonGitSetup(self):
    git1 = self._MakeRepo('git1', self.source)
    git2 = self._MakeRepo('git2', self.source)
    patch = self.CommitFile(git1, 'monkeys', 'foon')
    return git1, git2, patch

  def testFetch(self):
    git1, git2, patch = self._CommonGitSetup()
    patch.Fetch(git2)
    self.assertEqual(patch.sha1, self._GetSha1(git2, 'FETCH_HEAD'))
    # Verify reuse; specifically that Fetch doesn't actually run since
    # the rev is already available locally via alternates.
    patch.project_url = '/dev/null'
    git3 = self._MakeRepo('git3', git2)
    patch.Fetch(git3)
    self.assertEqual(patch.sha1, self._GetSha1(git3, patch.sha1))

  def testAlreadyApplied(self):
    git1 = self._MakeRepo('git1', self.source)
    patch = self.CommitFile(git1, 'monkeys', 'rule')
    # Note that apply switches to a separate branch; thus the
    # double apply.  The first lands the change, the second
    # verifies the machinery doesn't scream when we try
    # landing it a second time.
    patch.Apply(git1, self.DEFAULT_TRACKING)
    patch.Apply(git1, self.DEFAULT_TRACKING)

  def testCleanlyApply(self):
    git1, git2, patch = self._CommonGitSetup()
    # Clone git3 before we modify git2; else we'll just wind up
    # cloning it's master.
    git3 = self._MakeRepo('git3', git2)
    patch.Apply(git2, self.DEFAULT_TRACKING)
    self.assertEqual(patch.sha1, self._GetSha1(git2, 'HEAD'))
    # Verify reuse; specifically that Fetch doesn't actually run since
    # the object is available in alternates.  testFetch partially
    # validates this; the Apply usage here fully validates it via
    # ensuring that the attempted Apply goes boom if it can't get the
    # required sha1.
    patch.project_url='/dev/null'
    patch.Apply(git3, self.DEFAULT_TRACKING)
    self.assertEqual(patch.sha1, self._GetSha1(git3, 'HEAD'))

  def testFailsApply(self):
    git1, git2, patch1 = self._CommonGitSetup()
    patch2 = self.CommitFile(git2, 'monkeys', 'not foon')
    # Note that Apply creates it's own branch, resetting to master
    # thus we have to re-apply (even if it looks stupid, it's right).
    patch2.Apply(git2, self.DEFAULT_TRACKING)
    try:
      patch1.Apply(git2, self.DEFAULT_TRACKING)
    except cros_patch.ApplyPatchException, e:
      self.assertTrue(e.inflight)
    else:
      raise AssertionError("patch1.Apply didn't throw a failing "
                           "exception.")

  def _assertLookupAliases(self, internal):
    git1 = self._MakeRepo('git1', self.source)
    patch = self.CommitChangeIdFile(git1)
    patch.internal = internal
    prefix = '*' if internal else ''
    vals = [patch.change_id, patch.sha1, getattr(patch, 'gerrit_number', None),
            getattr(patch, 'original_sha1', None)]
    vals = [x for x in vals if x is not None]
    self.assertEqual(set(prefix + x for x in vals),
                     set(patch.LookupAliases()))

  def testExternalLookupAliases(self):
    self._assertLookupAliases(False)

  def testInternalLookupAliases(self):
    self._assertLookupAliases(True)

  def MakeChangeId(self, how_many=1):
    l = [cros_patch.MakeChangeId() for _ in xrange(how_many)]
    if how_many == 1:
      return l[0]
    return l

  def CommitChangeIdFile(self, repo, changeid=None, extra=None,
                         filename='monkeys', content='flinging',
                         raw_changeid_text=None, **kwargs):
    template = self.COMMIT_TEMPLATE
    if changeid is None:
      changeid = self.MakeChangeId()
    if raw_changeid_text is None:
      raw_changeid_text = 'Change-Id: %s' % (changeid,)
    if extra is None:
      extra = ''
    commit = template % {'change-id':raw_changeid_text, 'extra':extra}

    return self.CommitFile(repo, filename, content, commit=commit,
                           ChangeId=changeid, **kwargs)

  def _assertGerritDependencies(self, internal=False):
    convert = str
    if internal:
      convert = lambda val: '*%s' % (val,)
    git1 = self._MakeRepo('git1', self.source)
    # Check that we handle the edge case of the first commit in a
    # repo...
    patch = self._MkPatch(git1, self._GetSha1(git1, 'HEAD'), internal=internal)
    self.assertEqual(
        patch.GerritDependencies(git1, 'refs/remotes/origin/master'),
        [])
    cid1, cid2, cid3 = self.MakeChangeId(3)
    patch = self.CommitChangeIdFile(git1, cid1, internal=internal)
    # Since its parent is ToT, there are no deps.
    self.assertEqual(
        patch.GerritDependencies(git1, 'refs/remotes/origin/master'),
        [])
    patch = self.CommitChangeIdFile(git1, cid2, content='monkeys',
                                    internal=internal)
    self.assertEqual(
        patch.GerritDependencies(git1, 'refs/remotes/origin/master'),
        [convert(cid1)])

    # Check the behaviour for missing ChangeId in a parent next.
    patch = self.CommitChangeIdFile(git1, cid1, content='fling poo',
                                    raw_changeid_text='', internal=internal)

    # Verify it returns just the parrent, rather than all parents.
    self.assertEqual(
        patch.GerritDependencies(git1, 'refs/remotes/origin/master'),
        [convert(cid2)])

    parent_sha1 = patch.sha1
    # Verify if a Change-Id exists but is invalid, it's flagged.
    for content in ('asdfg', '%sg' % ('0' * 39)):
      patch = self.CommitChangeIdFile(git1, content='thus %s' % content,
                                      raw_changeid_text='Change-Id: I%s'
                                      % content, internal=internal)
      patch = self.CommitChangeIdFile(git1, cid3, content='update')
      # assertRaises doesn't allow us to specify the message, thus handle
      # this manually.
      try:
        patch.GerritDependencies(git1, 'refs/remotes/origin/master')
        raise AssertionError("Change-Id: I%s failed to trigger a BrokenChangeId"
                             % (content,))
      except cros_patch.BrokenChangeID:
        pass
      # Now wipe those commits since they'll interfere w/ the next run, and the
      # following code.
      cros_build_lib.RunGitCommand(git1, ['reset', '--hard', 'HEAD^^'])

    # Verify that if a ChangeId is lacking, it switches back to commit based
    # ids.
    patch = self.CommitChangeIdFile(git1, raw_changeid_text='',
                                    content='the glass walls.',
                                    internal=internal)
    self.assertEqual(
        patch.GerritDependencies(git1, 'refs/remotes/origin/master'),
        map(convert, [parent_sha1]))

  def testExternalGerritDependencies(self):
    self._assertGerritDependencies()

  def testInternalGerritDependencies(self):
    self._assertGerritDependencies(True)

  def _CheckPaladin(self, repo, master_id, ids, extra):
    patch = self.CommitChangeIdFile(
        repo, master_id, extra=extra,
        filename='paladincheck', content=str(_GetNumber()))
    deps = patch.PaladinDependencies(repo)
    # Assert that are parsing unique'ifies the results.
    self.assertEqual(len(deps), len(set(deps)))
    deps = set(deps)
    ids = set(ids)
    self.assertEqual(ids, deps)
    self.assertEqual(
        set(cros_patch.FormatPatchDep(x) for x in deps),
        set(cros_patch.FormatPatchDep(x) for x in ids))
    return patch

  def testPaladinDependencies(self):
    git1 = self._MakeRepo('git1', self.source)
    cid1, cid2, cid3, cid4 = self.MakeChangeId(4)
    # Verify it handles nonexistant CQ-DEPEND.
    self._CheckPaladin(git1, cid1, [], '')
    # Single key, single value.
    self._CheckPaladin(git1, cid1, [cid2],
                       'CQ-DEPEND=%s' % cid2)
    # Single key, gerrit number.
    self._CheckPaladin(git1, cid1, ['123'],
                       'CQ-DEPEND=%s' % 123)
    # Single key, gerrit number.
    self._CheckPaladin(git1, cid1, ['123456'],
                       'CQ-DEPEND=%s' % 123456)
    # Single key, gerrit number; ensure it
    # cuts off before a million changes (this
    # is done to avoid collisions w/ sha1 when
    # we're using shortened versions).
    self.assertRaises(cros_patch.BrokenCQDepends,
                      self._CheckPaladin, git1, cid1,
                      ['1234567'], 'CQ-DEPEND=%s' % '1234567')
    # Single key, gerrit number, internal.
    self._CheckPaladin(git1, cid1, ['*123'],
                       'CQ-DEPEND=%s' % '*123')
    # Ensure SHA1's aren't allowed.
    sha1 = '0' * 40
    self.assertRaises(cros_patch.BrokenCQDepends,
                      self._CheckPaladin, git1, cid1,
                      [sha1], 'CQ-DEPEND=%s' % sha1)

    # Single key, multiple values
    self._CheckPaladin(git1, cid1, [cid2, '1223'],
                       'CQ-DEPEND=%s %s' % (cid2, '1223'))
    # Dumb comma behaviour
    self._CheckPaladin(git1, cid1, [cid2, cid3],
                      'CQ-DEPEND=%s, %s,' % (cid2, cid3))
    # Multiple keys.
    self._CheckPaladin(git1, cid1, [cid2, '*245', cid4],
                      'CQ-DEPEND=%s, %s\nCQ-DEPEND=%s' % (cid2, '*245', cid4))

    # Ensure it goes boom on invalid data.
    self.assertRaises(cros_patch.BrokenCQDepends, self._CheckPaladin,
                      git1, cid1, [], 'CQ-DEPEND=monkeys')
    self.assertRaises(cros_patch.BrokenCQDepends, self._CheckPaladin,
                      git1, cid1, [], 'CQ-DEPEND=%s monkeys' % (cid2,))
    # Validate numeric is allowed.
    self._CheckPaladin(git1, cid1, [cid2, '1'], 'CQ-DEPEND=1 %s' % cid2)
    # Validate that it unique'ifies the results.
    self._CheckPaladin(git1, cid1, ['1'], 'CQ-DEPEND=1 1')


class TestLocalPatchGit(TestGitRepoPatch):

  patch_kls = cros_patch.LocalPatch

  def setUp(self):
    TestGitRepoPatch.setUp(self)
    self.sourceroot = os.path.join(self.tempdir, 'sourceroot')


  def _MkPatch(self, source, sha1, ref='refs/heads/master', **kwds):
    return self.patch_kls(source, 'chromiumos/chromite', ref,
                          'origin/master', kwds.pop('internal', False),
                          sha1, **kwds)

  def testUpload(self):
    def ProjectDirMock(sourceroot):
      return git1

    git1, git2, patch = self._CommonGitSetup()

    git2_sha1 = self._GetSha1(git2, 'HEAD')

    patch.ProjectDir = ProjectDirMock
    # First suppress carbon copy behaviour so we verify pushing
    # plain works.
    sha1 = patch.sha1
    patch._GetCarbonCopy = lambda: sha1
    patch.Upload(git2, 'refs/testing/test1')
    self.assertEqual(self._GetSha1(git2, 'refs/testing/test1'),
                     patch.sha1)

    # Enable CarbonCopy behaviour; verify it lands a different
    # sha1.  Additionally verify it didn't corrupt the patch's sha1 locally.
    del patch._GetCarbonCopy
    patch.Upload(git2, 'refs/testing/test2')
    self.assertNotEqual(self._GetSha1(git2, 'refs/testing/test2'),
                        patch.sha1)
    self.assertEqual(patch.sha1, sha1)
    # Ensure the carbon creation didn't damage the target repo.
    self.assertEqual(self._GetSha1(git1, 'HEAD'), sha1)

    # Ensure we didn't damage the target repo's state at all.
    self.assertEqual(git2_sha1, self._GetSha1(git2, 'HEAD'))
    # Ensure the content is the same.
    base = ['git', 'show']
    self.assertEqual(
        self._run(base + ['refs/testing/test1:monkeys'], git2),
        self._run(base + ['refs/testing/test2:monkeys'], git2))
    base = ['git', 'log', '--format=%B', '-n1']
    self.assertEqual(
        self._run(base + ['refs/testing/test1'], git2),
        self._run(base + ['refs/testing/test2'], git2))


class TestUploadedLocalPatch(TestGitRepoPatch):

  PROJECT = 'chromiumos/chromite'
  ORIGINAL_BRANCH = 'original_branch'
  ORIGINAL_SHA1 = 'ffffffff'.ljust(40, '0')

  patch_kls = cros_patch.UploadedLocalPatch

  def _MkPatch(self, source, sha1, ref='refs/heads/master', **kwds):
    return self.patch_kls(source, self.PROJECT, ref,
                          'origin/master', self.ORIGINAL_BRANCH,
                          self.ORIGINAL_SHA1, kwds.pop('internal', False),
                          carbon_copy_sha1=sha1, **kwds)

  def testStringRepresentation(self):
    git1, git2, patch = self._CommonGitSetup()
    str_rep = str(patch).split(':')
    for element in [self.PROJECT, self.ORIGINAL_BRANCH, self.ORIGINAL_SHA1[:8]]:
      self.assertTrue(element in str_rep,
                      msg="Couldn't find %s in %s" % (element, str_rep))


class TestGerritPatch(TestGitRepoPatch):

  has_native_change_id = True

  class patch_kls(cros_patch.GerritPatch):
    # Suppress the behaviour pointing the project url at actual gerrit,
    # instead slaving it back to a local repo for tests.
    def _GetProjectUrl(self, project, internal):
      assert hasattr(self, 'patch_dict')
      return self.patch_dict['_unittest_url_bypass']

  def test_GetProjectUrl(self):
    # We test this since we explicitly override the behaviour
    # for all other usage.
    kls = cros_patch.GerritPatch
    self.assertEqual(
        kls._GetProjectUrl('monkeys', False),
        os.path.join(kls._PUBLIC_URL, 'monkeys'))
    self.assertEqual(
        kls._GetProjectUrl('monkeys', True),
        os.path.join(constants.GERRIT_INT_SSH_URL, 'monkeys'))

  @property
  def test_json(self):
    return copy.deepcopy(FAKE_PATCH_JSON)

  def _MkPatch(self, source, sha1, ref='refs/heads/master', **kwds):
    json = self.test_json
    internal = kwds.pop('internal', False)
    suppress_branch = kwds.pop('suppress_branch', False)
    change_id = kwds.pop('ChangeId', None)
    if change_id is None:
      change_id = self.MakeChangeId()
    json.update(kwds)
    change_num, patch_num = _GetNumber(), _GetNumber()
    # Note we intentionally use a gerrit like refspec here; we want to
    # ensure that none of our common code pathways puke on a non head/tag.
    refspec = 'refs/changes/%i/%i/%i' % (
        change_num % 100, change_num + 1000, patch_num)
    json['currentPatchSet'].update(
        dict(number=patch_num, ref=refspec, revision=sha1))
    json['branch'] = os.path.basename(ref)
    json['_unittest_url_bypass'] = source
    json['id'] = change_id

    obj = self.patch_kls(json.copy(), internal)
    self.assertEqual(obj.patch_dict, json)
    self.assertEqual(obj.internal, internal)
    self.assertEqual(obj.project, json['project'])
    self.assertEqual(obj.ref, refspec)
    self.assertEqual(obj.change_id, change_id)
    self.assertEqual(
        obj.id, cros_patch.FormatChangeId(change_id, force_internal=internal))
    # Now make the fetching actually work, if desired.
    if not suppress_branch:
      # Note that a push is needed here, rather than a branch; branch
      # will just make it under refs/heads, we want it literally in
      # refs/changes/
      self._run(['git', 'push', source, '%s:%s' % (sha1, refspec)], source)
    return obj

  def testIsAlreadyMerged(self):
    # Note that these are magic constants- they're known to be
    # merged (and the other abandoned) in public gerrit.
    # If old changes are ever flushed, or something 'special' occurs,
    # then this will break.  That it's an acceptable risk.
    # Note we should be checking a known open one; seems rather likely
    # that'll get closed inadvertantly thus breaking the tests (not
    # an acceptable risk in the authors opinion).
    merged, abandoned, still_open = gerrit_helper.GetGerritPatchInfo(
        [GERRIT_MERGED_CHANGEID, GERRIT_ABANDONED_CHANGEID,
         GERRIT_OPEN_CHANGEID])
    self.assertTrue(merged.IsAlreadyMerged())
    self.assertFalse(abandoned.IsAlreadyMerged())
    self.assertFalse(still_open.IsAlreadyMerged())

  @property
  def test_json(self):
    return copy.deepcopy(FAKE_PATCH_JSON)


class PrepareRemotePatchesTest(unittest.TestCase):

  def MkRemote(self,
               project='my/project', original_branch='my-local',
               ref='refs/tryjobs/elmer/patches', tracking_branch='master',
               internal=False):

    l = [project, original_branch, ref, tracking_branch,
         getattr(constants, '%s_PATCH_TAG' % (
            'INTERNAL' if internal else 'EXTERNAL'))]
    return ':'.join(l)

  def assertRemote(self, patch, project='my/project',
                   original_branch='my-local',
                   ref='refs/tryjobs/elmer/patches', tracking_branch='master',
                   internal=False):
    self.assertEqual(patch.project, project)
    self.assertEqual(patch.original_branch, original_branch)
    self.assertEqual(patch.ref, ref)
    self.assertEqual(patch.tracking_branch, tracking_branch)
    self.assertEqual(patch.internal, internal)

  def test(self):
    # Check handling of a single patch...
    patches = cros_patch.PrepareRemotePatches([self.MkRemote()])
    self.assertEqual(len(patches), 1)
    self.assertRemote(patches[0])

    # Check handling of a multiple...
    patches = cros_patch.PrepareRemotePatches(
        [self.MkRemote(), self.MkRemote(project='foon')])
    self.assertEqual(len(patches), 2)
    self.assertRemote(patches[0])
    self.assertRemote(patches[1], project='foon')

    # Ensure basic validation occurs:
    chunks = self.MkRemote().split(':')
    self.assertRaises(ValueError, cros_patch.PrepareRemotePatches,
                      ':'.join(chunks[:-1]))
    self.assertRaises(ValueError, cros_patch.PrepareRemotePatches,
                      ':'.join(chunks[:-1] + ['monkeys']))
    self.assertRaises(ValueError, cros_patch.PrepareRemotePatches,
                      ':'.join(chunks + [':']))


class PrepareLocalPatchesTests(mox.MoxTestBase):

  def setUp(self):
    mox.MoxTestBase.setUp(self)

    self.patches = ['my/project:mybranch']

    self.mox.StubOutWithMock(cros_build_lib, 'GetProjectDir')
    self.mox.StubOutWithMock(cros_build_lib, 'GetCurrentBranch')
    self.mox.StubOutWithMock(cros_build_lib, 'RunCommand')
    self.mox.StubOutWithMock(cros_build_lib, 'RunGitCommand')
    self.manifest = self.mox.CreateMock(cros_build_lib.ManifestCheckout)

  def VerifyPatchInfo(self, patch_info, project, branch, tracking_branch):
    """Check the returned GitRepoPatchInfo against golden values."""
    self.assertEquals(patch_info.project, project)
    self.assertEquals(patch_info.ref, branch)
    self.assertEquals(patch_info.tracking_branch, tracking_branch)

  def testBranchSpecifiedSuccessRun(self):
    """Test success with branch specified by user."""
    output_obj = self.mox.CreateMock(cros_build_lib.CommandResult)
    output_obj.output = '12345'.rjust(40, '0')
    self.manifest.GetProjectPath('my/project', True).AndReturn('mydir')
    self.manifest.GetProjectsLocalRevision('my/project').AndReturn('m/kernel')
    self.manifest.ProjectIsInternal('my/project').AndReturn(False)
    cros_build_lib.RunGitCommand(
        'mydir', mox.In('m/kernel..mybranch')).AndReturn(output_obj)

    # Suppress the normal parse machinery.
    self.mox.StubOutWithMock(cros_patch.LocalPatch, 'Fetch')
    # pylint: disable=E1120
    cros_patch.LocalPatch.Fetch('mydir/.git').AndReturn(output_obj)
    self.mox.ReplayAll()

    patch_info = cros_patch.PrepareLocalPatches(self.manifest, self.patches)
    self.VerifyPatchInfo(patch_info[0], 'my/project', 'mybranch', 'kernel')
    self.mox.VerifyAll()

  def testBranchSpecifiedNoChanges(self):
    """Test when no changes on the branch specified by user."""
    output_obj = self.mox.CreateMock(cros_build_lib.CommandResult)
    output_obj.output = ''
    self.manifest.GetProjectPath('my/project', True).AndReturn('mydir')
    self.manifest.GetProjectsLocalRevision('my/project').AndReturn('m/master')
    self.manifest.ProjectIsInternal('my/project').AndReturn(False)
    cros_build_lib.RunGitCommand(
        'mydir', mox.In('m/master..mybranch')).AndReturn(output_obj)
    self.mox.ReplayAll()

    self.assertRaises(
        SystemExit,
        cros_patch.PrepareLocalPatches,
        self.manifest, self.patches)


class ApplyLocalPatchesTests(mox.MoxTestBase):

  def testWrongTrackingBranch(self):
    """When the original patch branch does not track buildroot's branch."""

    # Use external patches for this test (thus the False).
    patch = cros_patch.GitRepoPatch('/path/to/my/project.git',
                                    'my/project', 'mybranch',
                                    'master', False)
    self.assertRaises(cros_patch.PatchException, patch.Apply,
                      '/tmp/notadirectory', 'origin/R19')

if __name__ == '__main__':
  logging_format = '%(asctime)s - %(filename)s - %(levelname)-8s: %(message)s'
  date_format = constants.LOGGER_DATE_FMT
  logging.basicConfig(level=logging.DEBUG, format=logging_format,

                      datefmt=date_format)
  unittest.main()
