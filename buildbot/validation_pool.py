# Copyright (c) 2011-2012 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Module that handles interactions with a Validation Pool.

The validation pool is the set of commits that are ready to be validated i.e.
ready for the commit queue to try.
"""

import json
import logging
import os
import time
import urllib
from xml.dom import minidom

from chromite.buildbot import gerrit_helper
from chromite.buildbot import lkgm_manager
from chromite.buildbot import patch as cros_patch
from chromite.lib import cros_build_lib

_BUILD_DASHBOARD = 'http://build.chromium.org/p/chromiumos'
_BUILD_INT_DASHBOARD = 'http://chromegw/i/chromeos'


def _RunCommand(cmd, dryrun):
  """Runs the specified shell cmd if dryrun=False."""
  if dryrun:
    logging.info('Would have run: %s', ' '.join(cmd))
  else:
    cros_build_lib.RunCommand(cmd, error_ok=True)


class TreeIsClosedException(Exception):
  """Raised when the tree is closed and we wanted to submit changes."""
  def __init__(self):
    super(TreeIsClosedException, self).__init__(
        'TREE IS CLOSED.  PLEASE SET TO OPEN OR THROTTLED TO COMMIT')


class FailedToSubmitAllChangesException(Exception):
  """Raised if we fail to submit any changes."""
  def __init__(self, changes):
    super(FailedToSubmitAllChangesException, self).__init__(
        'FAILED TO SUBMIT ALL CHANGES:  Could not verify that changes %s were '
        'submitted' % ' '.join(str(c) for c in changes))


class ValidationPool(object):
  """Class that handles interactions with a validation pool.

  This class can be used to acquire a set of commits that form a pool of
  commits ready to be validated and committed.

  Usage:  Use ValidationPoo.AcquirePool -- a static
  method that grabs the commits that are ready for validation.
  """

  DEFAULT_ERROR_APPLY_MESSAGE = ('Please re-sync, rebase, and re-upload '
                                 'your change.')

  GLOBAL_DRYRUN = False

  def __init__(self, internal, build_number, builder_name, is_master, dryrun,
               changes=None, non_os_changes=None,
               conflicting_changes=None):
    """Initializes an instance by setting default valuables to instance vars.

    Generally use AcquirePool as an entry pool to a pool rather than this
    method.

    Args:
      internal:  Set to True if this is an internal validation pool.
      build_number:  Build number for this validation attempt.
      builder_name:  Builder name on buildbot dashboard.
      is_master: True if this is the master builder for the Commit Queue.
      dryrun: If set to True, do not submit anything to Gerrit.
    Optional Args:
      changes: List of changes for this validation pool.
      non_manifest_changes: List of changes that are part of this validation
        pool but aren't part of the cros checkout.
      changes_that_failed_to_apply_earlier: Changes that failed to apply but
        we're keeping around because they conflict with other changes in
        flight.
    """
    build_dashboard = _BUILD_DASHBOARD if not internal else _BUILD_INT_DASHBOARD
    self.build_log = '%s/builders/%s/builds/%s' % (
        build_dashboard, builder_name, str(build_number))
    self.gerrit_helper = gerrit_helper.GerritHelper(internal)
    self.is_master = is_master
    self.dryrun = dryrun | self.GLOBAL_DRYRUN

    # See optional args for types of changes.
    self.changes = changes or []
    self.non_manifest_changes = non_os_changes or []
    self.changes_that_failed_to_apply_earlier = conflicting_changes or []

    # Private vars only used for pickling.
    self._internal = internal
    self._build_number = build_number
    self._builder_name = builder_name
    self._content_merging_projects = None

  def __getnewargs__(self):
    """Used for pickling to re-create validation pool."""
    return (self._internal, self._build_number, self._builder_name,
            self.is_master, self.dryrun, self.changes,
            self.non_manifest_changes,
            self.changes_that_failed_to_apply_earlier)

  @classmethod
  def _IsTreeOpen(cls, max_timeout=600):
    """Returns True if the tree is open or throttled.

    At the highest level this function checks to see if the Tree is Open.
    However, it also does a robustified wait as the server hosting the tree
    status page is known to be somewhat flaky and these errors can be handled
    with multiple retries.  In addition, it waits around for the Tree to Open
    based on |max_timeout| to give a greater chance of returning True as it
    expects callees to want to do some operation based on a True value.
    If a caller is not interested in this feature they should set |max_timeout|
    to 0.
    """
    state_field = 'general_state'
    # Limit sleep interval to the set of 1-30
    sleep_timeout = min(max(max_timeout/5, 1), 30)

    def _SleepWithExponentialBackOff(current_sleep):
      """Helper function to sleep with exponential backoff."""
      time.sleep(current_sleep)
      return current_sleep * 2

    def _GetTreeStatus(status_url):
      """Returns the JSON dictionary response from the status url."""
      max_attempts = 5
      current_sleep = 1
      for _ in range(max_attempts):
        try:
          # Check for successful response code.
          response = urllib.urlopen(status_url)
          if response.getcode() == 200:
            return json.load(response)

        # We remain robust against IOError's and retry.
        except IOError:
          pass

        current_sleep = _SleepWithExponentialBackOff(current_sleep)
      else:
        # We go ahead and say the tree is open if we can't get the status.
        logging.warn('Could not get a status from %s', status_url)
        return {state_field: 'open'}

    def _CanSubmit(json_dict):
      """Checks the json dict to determine whether the tree is open."""
      return json_dict[state_field] in ['open', 'throttled']

    # Check before looping with timeout.
    status_url = 'https://chromiumos-status.appspot.com/current?format=json'
    start_time = time.time()
    if _CanSubmit(_GetTreeStatus(status_url)):
      return True

    # Loop until either we run out of time or the tree is open.
    while (time.time() - start_time) < max_timeout:
      if _CanSubmit(_GetTreeStatus(status_url)):
        return True
      else:
        time.sleep(sleep_timeout)

    return False

  @classmethod
  def AcquirePool(cls, internal, buildroot, build_number, builder_name, dryrun):
    """Acquires the current pool from Gerrit.

    Polls Gerrit and checks for which change's are ready to be committed.

    Args:
      internal: If True, use gerrit-int.
      buildroot: The location of the buildroot used to filter projects.
      build_number: Corresponding build number for the build.
      builder_name:  Builder name on buildbot dashboard.
      dryrun: Don't submit anything to gerrit.
    Returns:
      ValidationPool object.
    Raises:
      TreeIsClosedException: if the tree is closed.
    """
    # We choose a longer wait here as we haven't committed to anything yet. By
    # doing this here we can reduce the number of builder cycles.
    if dryrun or cls._IsTreeOpen(max_timeout=3600):
      # Only master configurations should call this method.
      pool = ValidationPool(internal, build_number, builder_name, True, dryrun)
      pool.gerrit_helper = gerrit_helper.GerritHelper(internal)
      raw_changes = pool.gerrit_helper.GrabChangesReadyForCommit()
      changes, non_manifest_changes = ValidationPool._FilterNonCrosProjects(
          raw_changes, buildroot)
      pool.changes, pool.non_manifest_changes = changes, non_manifest_changes
      return pool
    else:
      raise TreeIsClosedException()

  @classmethod
  def AcquirePoolFromManifest(cls, manifest, internal, build_number,
                              builder_name, is_master, dryrun):
    """Acquires the current pool from a given manifest.

    Args:
      manifest: path to the manifest where the pool resides.
      internal: if true, assume gerrit-int.
      build_number: Corresponding build number for the build.
      builder_name:  Builder name on buildbot dashboard.
      is_master: Boolean that indicates whether this is a pool for a master.
        config or not.
      dryrun: Don't submit anything to gerrit.
    Returns:
      ValidationPool object.
    """
    pool = ValidationPool(internal, build_number, builder_name, is_master,
                          dryrun)
    pool.gerrit_helper = gerrit_helper.GerritHelper(internal)
    manifest_dom = minidom.parse(manifest)
    pending_commits = manifest_dom.getElementsByTagName(
        lkgm_manager.PALADIN_COMMIT_ELEMENT)
    for pending_commit in pending_commits:
      project = pending_commit.getAttribute(lkgm_manager.PALADIN_PROJECT_ATTR)
      change = pending_commit.getAttribute(lkgm_manager.PALADIN_CHANGE_ID_ATTR)
      commit = pending_commit.getAttribute(lkgm_manager.PALADIN_COMMIT_ATTR)
      pool.changes.append(pool.gerrit_helper.GrabPatchFromGerrit(
          project, change, commit))

    return pool

  @property
  def ContentMergingProjects(self):
    val = self._content_merging_projects
    if val is None:
      val = self.gerrit_helper.FindContentMergingProjects()
      self._content_merging_projects = val
    return val

  @staticmethod
  def _FilterNonCrosProjects(changes, buildroot):
    """Filters changes to a tuple of relevant changes.

    There are many code reviews that are not part of Chromium OS and/or
    only relevant on a different branch. This method returns a tuple of (
    relevant reviews in a manifest, relevant reviews not in the manifest). Note
    that this function must be run while chromite is checked out in a
    repo-managed checkout.

    Args:
      changes:  List of GerritPatch objects.
      buildroot:  Buildroot containing manifest to filter against.

    Returns tuple of
      relevant reviews in a manifest, relevant reviews not in the manifest.
    """

    def IsCrosReview(change):
      return (change.project.startswith('chromiumos') or
              change.project.startswith('chromeos'))

    # First we filter to only Chromium OS repositories.
    changes = [c for c in changes if IsCrosReview(c)]

    manifest_path = os.path.join(buildroot, '.repo', 'manifests/full.xml')
    handler = cros_build_lib.ManifestHandler.ParseManifest(manifest_path)
    projects = handler.projects

    changes_in_manifest = []
    changes_not_in_manifest = []
    for change in changes:
      branch = handler.default.get('revision')
      patch_branch = 'refs/heads/%s' % change.tracking_branch
      project = projects.get(change.project)
      if project:
        branch = project.get('revision') or branch

      if branch == patch_branch:
        if project:
          changes_in_manifest.append(change)
        else:
          changes_not_in_manifest.append(change)
      else:
        logging.info('Filtered change %s', change)

    return changes_in_manifest, changes_not_in_manifest

  def ApplyPoolIntoRepo(self, buildroot):
    """Applies changes from pool into the directory specified by the buildroot.

    This method applies changes in the order specified.  It also respects
    dependency order.

    Returns:
      True if we managed to apply any changes.
    """
    # Sets are used for performance reasons where changes_list is used to
    # maintain ordering when applying changes.
    changes_that_failed_to_apply_against_other_changes = set()
    changes_that_failed_to_apply_to_tot = set()
    changes_applied = set()
    changes_list = []

    # Maps Change numbers to GerritPatch object for lookup of dependent
    # changes.
    change_map = dict((change.id, change) for change in self.changes)
    for change in self.changes:
      logging.debug('Trying change %s', change.id)
      # We've already attempted this change because it was a dependent change
      # of another change that was ready.
      if (change in changes_that_failed_to_apply_to_tot or
          change in changes_applied):
        continue

      # Change stacks consists of the change plus its dependencies in the order
      # that they should be applied.
      change_stack = [change]
      apply_chain = True
      deps = []
      try:
        deps.extend(change.GerritDependencies(buildroot))
        deps.extend(change.PaladinDependencies(buildroot))
      except cros_patch.MissingChangeIDException as me:
        change.apply_error_message = (
            'Could not apply change %s because change has a Gerrit Dependency '
            'that does not contain a ChangeId.  Please remove this dependency '
            'or update the dependency with a ChangeId.' % change.id)
        logging.error(change.apply_error_message)
        logging.error(str(me))
        changes_that_failed_to_apply_to_tot.add(change)
        apply_chain = False

      for dep in deps:
        dep_change = change_map.get(dep)
        if not dep_change:
          # The dep may have been committed already.
          if not self.gerrit_helper.IsChangeCommitted(dep, must_match=False):
            message = ('Could not apply change %s because dependent '
                       'change %s is not ready to be committed.' % (
                        change.id, dep))
            logging.info(message)
            change.apply_error_message = message
            apply_chain = False
            break
        else:
          change_stack.insert(0, dep_change)

      # Should we apply the chain -- i.e. all deps are ready.
      if not apply_chain:
        continue

      # Apply changes in change_stack.  For chains that were aborted early,
      # we still want to apply changes in change_stack because they were
      # ready to be committed (o/w wouldn't have been in the change_map).
      for change in change_stack:
        try:
          if change in changes_applied:
            continue
          elif change in changes_that_failed_to_apply_to_tot:
            break
          # If we're in dryrun mode, then 3way is always allowed.
          # Otherwise, allow 3way only if the gerrit project allows it.
          if self.dryrun:
            trivial = False
          else:
            trivial = change.project not in self.ContentMergingProjects
          change.Apply(buildroot, trivial=trivial)

        except cros_patch.ApplyPatchException as e:
          if e.type == cros_patch.ApplyPatchException.TYPE_REBASE_TO_TOT:
            changes_that_failed_to_apply_to_tot.add(change)
          else:
            change.apply_error_message = (
                'Your change conflicted with another change being tested '
                'in the last validation pool.  Please re-sync, rebase and '
                're-upload.')
            changes_that_failed_to_apply_against_other_changes.add(change)

          break
        else:
          # We applied the change successfully.
          changes_applied.add(change)
          changes_list.append(change)
          lkgm_manager.PrintLink(str(change), change.url)
          if self.is_master:
            self.HandleApplied(change)

    if changes_applied:
      logging.debug('Done investigating changes.  Applied %s',
                    ' '.join([c.id for c in changes_applied]))

    if changes_that_failed_to_apply_to_tot:
      logging.info('Changes %s could not be applied cleanly.',
                  ' '.join([c.id for c in changes_that_failed_to_apply_to_tot]))
      self.HandleApplicationFailure(changes_that_failed_to_apply_to_tot)

    self.changes = changes_list
    self.changes_that_failed_to_apply_earlier = list(
        changes_that_failed_to_apply_against_other_changes)
    return len(self.changes) > 0

  def _SubmitChanges(self, changes):
    """Submits given changes to Gerrit.

    Raises:
      TreeIsClosedException: if the tree is closed.
      FailedToSubmitAllChangesException: if we can't submit a change.
    """
    assert self.is_master, 'Non-master builder calling SubmitPool'
    changes_that_failed_to_submit = []
    # We use the default timeout here as while we want some robustness against
    # the tree status being red i.e. flakiness, we don't want to wait too long
    # as validation can become stale.
    if self.dryrun or ValidationPool._IsTreeOpen():
      for change in changes:
        was_change_submitted = False
        logging.info('Change %s will be submitted', change)
        try:
          self.SubmitChange(change)
          was_change_submitted = self.gerrit_helper.IsChangeCommitted(
                change.id, self.dryrun)
        except cros_build_lib.RunCommandError:
          logging.error('gerrit review --submit failed for change.')
        finally:
          if not was_change_submitted:
            logging.error('Could not submit %s', str(change))
            self.HandleCouldNotSubmit(change)
            changes_that_failed_to_submit.append(change)

      if changes_that_failed_to_submit:
        raise FailedToSubmitAllChangesException(changes_that_failed_to_submit)

    else:
      raise TreeIsClosedException()

  def SubmitChange(self, change):
    """Submits patch using Gerrit Review.

    Args:
      helper: Instance of gerrit_helper for the gerrit instance.
      dryrun: If true, do not actually commit anything to Gerrit.
    """
    cmd = self.gerrit_helper.GetGerritReviewCommand(['--submit', '%s,%s' % (
        change.gerrit_number, change.patch_number)])
    _RunCommand(cmd, self.dryrun)

  def SubmitNonManifestChanges(self):
    """Commits changes to Gerrit from Pool that aren't part of the checkout.

    Raises:
      TreeIsClosedException: if the tree is closed.
      FailedToSubmitAllChangesException: if we can't submit a change.
    """
    self._SubmitChanges(self.non_manifest_changes)

  def SubmitPool(self):
    """Commits changes to Gerrit from Pool.  This is only called by a master.

    Raises:
      TreeIsClosedException: if the tree is closed.
      FailedToSubmitAllChangesException: if we can't submit a change.
    """
    self._SubmitChanges(self.changes)
    if self.changes_that_failed_to_apply_earlier:
      self.HandleApplicationFailure(self.changes_that_failed_to_apply_earlier)

  def HandleApplicationFailure(self, changes):
    """Handles changes that were not able to be applied cleanly."""
    for change in changes:
      logging.info('Change %s did not apply cleanly.', change.id)
      if self.is_master:
        self.HandleCouldNotApply(change)

  def HandleValidationFailure(self):
    """Handles failed changes by removing them from next Validation Pools."""
    logging.info('Validation failed for all changes.')
    for change in self.changes:
      logging.info('Validation failed for change %s.', change)
      self.HandleCouldNotVerify(change)

  def _SendNotification(self, change, msg):
    msg %= {'build_log':self.build_log}
    PaladinMessage(msg, change, self.gerrit_helper).Send(self.dryrun)

  def HandleCouldNotSubmit(self, change):
    """Handler that is called when Paladin can't submit a change.

    This should be rare, but if an admin overrides the commit queue and commits
    a change that conflicts with this change, it'll apply, build/validate but
    receive an error when submitting.

    Args:
      change: GerritPatch instance to operate upon.
    """
    self._SendNotification(change,
        'The Commit Queue failed to submit your change in %(build_log)s . '
        'This can happen if you submitted your change or someone else '
        'submitted a conflicting change while your change was being tested.')
    self.gerrit_helper.RemoveCommitReady(change, dryrun=self.dryrun)

  def HandleCouldNotVerify(self, change):
    """Handler for when Paladin fails to validate a change.

    This handler notifies set Verified-1 to the review forcing the developer
    to re-upload a change that works.  There are many reasons why this might be
    called e.g. build or testing exception.

    Args:
      change: GerritPatch instance to operate upon.
    """
    self._SendNotification(change,
        'The Commit Queue failed to verify your change in %(build_log)s . '
        'If you believe this happened in error, just re-mark your commit as '
        'ready. Your change will then get automatically retried.')
    self.gerrit_helper.RemoveCommitReady(change, dryrun=self.dryrun)

  def HandleCouldNotApply(self, change):
    """Handler for when Paladin fails to apply a change.

    This handler notifies set CodeReview-2 to the review forcing the developer
    to re-upload a rebased change.

    Args:
      change: GerritPatch instance to operate upon.
    """
    msg = 'The Commit Queue failed to apply your change in %(build_log)s . '
    # This is written this way so that mox doesn't complain if/when we try
    # accessing an attr that doesn't exist.
    extra_msg = getattr(change, 'apply_error_message', None)
    if extra_msg is None:
      extra_msg = self.DEFAULT_ERROR_APPLY_MESSAGE

    msg += extra_msg
    self._SendNotification(change, msg)
    self.gerrit_helper.RemoveCommitReady(change, dryrun=self.dryrun)

  def HandleApplied(self, change):
    """Handler for when Paladin successfully applies a change.

    This handler notifies a developer that their change is being tried as
    part of a Paladin run defined by a build_log.

    Args:
      change: GerritPatch instance to operate upon.
    """
    self._SendNotification(change,
        'The Commit Queue has picked up your change. '
        'You can follow along at %(build_log)s .')


class PaladinMessage():
  """An object that is used to send messages to developers about their changes.
  """
  # URL where Paladin documentation is stored.
  _PALADIN_DOCUMENTATION_URL = ('http://www.chromium.org/developers/'
                                'tree-sheriffs/sheriff-details-chromium-os/'
                                'commit-queue-overview')

  def __init__(self, message, patch, helper):
    self.message = message
    self.patch = patch
    self.helper = helper

  def _ConstructPaladinMessage(self):
    """Adds any standard Paladin messaging to an existing message."""
    return self.message + (' Please see %s for more information.' %
                           self._PALADIN_DOCUMENTATION_URL)

  def Send(self, dryrun):
    """Sends the message to the developer."""
    cmd = self.helper.GetGerritReviewCommand(
        ['-m', '"%s"' % self._ConstructPaladinMessage(),
         '%s,%s' % (self.patch.gerrit_number, self.patch.patch_number)])
    _RunCommand(cmd, dryrun)
