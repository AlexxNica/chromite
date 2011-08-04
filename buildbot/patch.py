# Copyright (c) 2011 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Module that handles the processing of patches to the source tree."""

import constants
import glob
import json
import os
import shutil
import tempfile

from chromite.lib import cros_build_lib as cros_lib

# The prefix of the temporary directory created to store local patches.
_TRYBOT_TEMP_PREFIX = 'trybot_patch-'


class PatchException(Exception):
  """Exception thrown by GetGerritPatchInfo."""
  pass

class ApplyPatchException(Exception):
  """Exception thrown if we fail to apply a patch."""
  pass


class Patch(object):
  def __init__(self, project, tracking_branch):
    """Initialization of abstract Patch class.
    Args:
      project: The name of the project that the patch applies to.
      tracking_branch:  The remote branch of the project the patch applies to.
    """
    self.project = project
    self.tracking_branch = tracking_branch

  def Apply(self, buildroot):
    """Applies the patch to specified buildroot. Implement in subclasses.

    Raises:
      PatchException
    """
    raise NotImplementedError, 'Applies the patch to specified buildroot.'


class GerritPatch(Patch):
  """Object that represents a Gerrit CL."""
  _PUBLIC_URL = os.path.join(constants.GERRIT_HTTP_URL, 'gerrit/p')

  def __init__(self, patch_dict, internal):
    """Construct a GerritPatch object from Gerrit query results

    Args:
      patch_dict: A dictionary containing the parsed JSON gerrit query results.
      internal: Whether the CL is an internal CL.
    """
    Patch.__init__(self, patch_dict['project'], patch_dict['branch'])
    self.internal = internal
    # id - The CL's ChangeId
    self.id = patch_dict['id']
    # ref - The remote ref that contains the patch.
    self.ref = patch_dict['currentPatchSet']['ref']
    # revision - The CL's SHA1 hash.
    self.revision = patch_dict['currentPatchSet']['revision']

  def Apply(self, buildroot):
    """Implementation of Patch.Apply()."""
    if self.internal:
      url_prefix = constants.GERRIT_INT_SSH_URL
    else:
      url_prefix = self._PUBLIC_URL

    url = os.path.join(url_prefix, self.project)

    project_dir = cros_lib.GetProjectDir(buildroot, self.project)

    try:
      cros_lib.RunCommand(['git', 'fetch', url, self.ref], cwd=project_dir)
      cros_lib.RunCommand(['git', 'checkout', '--no-track',
                           '-b', constants.PATCH_BRANCH,
                           'FETCH_HEAD'], cwd=project_dir)

      manifest_default_branch = cros_lib.GetManifestDefaultBranch(buildroot)
      cros_lib.RunCommand(['git', 'rebase', 'm/' + manifest_default_branch],
                          cwd=project_dir)
    except cros_lib.RunCommandError as e:
      raise ApplyPatchException(e)

  def __str__(self):
    """Returns custom string to identify this patch."""
    return '%s:%s' % (self.project, self.id)



def RemovePatchRoot(patch_root):
  """Removes the temporary directory storing patches"""
  assert(os.path.basename(patch_root).startswith(_TRYBOT_TEMP_PREFIX))
  shutil.rmtree(patch_root)


class LocalPatch(Patch):
  """Object that represents a set of local commits that will be patched."""
  def __init__(self, project, tracking_branch, patch_dir, local_branch):
    """Construct a LocalPatch object.

    Args:
      project: Same as Patch constructor arg.
      tracking_branch: Same as Patch constructor arg.
      patch_dir: The directory where the .patch files are stored.
      local_branch:  The local branch of the project that the patch came from.
    """
    Patch.__init__(self, project, tracking_branch)
    self.patch_dir = patch_dir
    self.local_branch = local_branch

  def _GetFileList(self):
    """Return a list of .patch files in sorted order."""
    file_list = glob.glob(os.path.join(self.patch_dir, '*'))
    file_list.sort()
    return file_list

  def Apply(self, buildroot):
    """Implementation of Patch.Apply().

    Raises:
      PatchException if the patch is for the wrong tracking branch.
    """
    manifest_branch = _GetProjectManifestBranch(buildroot, self.project)
    if self.tracking_branch != manifest_branch:
      raise PatchException('branch %s for project %s is not tracking %s'
                           % (self.local_branch, self.project,
                              manifest_branch))

    project_dir = cros_lib.GetProjectDir(buildroot, self.project)
    try:
      cros_lib.RunCommand(['repo', 'start', constants.PATCH_BRANCH, '.'],
                          cwd=project_dir)
      cros_lib.RunCommand(['git', 'am', '--3way'] + self._GetFileList(),
                          cwd=project_dir)
    except cros_lib.RunCommandError as e:
      raise ApplyPatchException(e)


def GetGerritPatchInfo(patches):
  """Query Gerrit server for patch information.

  Args:
    patches: a list of patch ID's to query.  Internal patches start with a '*'.

  Returns:
    A list of GerritPatch objects describing each patch.

  Raises:
    PatchException if a patch can't be found.
  """
  parsed_patches = []
  for patch in patches:
    if patch.startswith('*'):
      # Internal CL's have a '*' in front
      internal = True
      server, port = constants.GERRIT_INT_HOST, constants.GERRIT_INT_PORT
      patch = patch[1:]
    else:
      internal = False
      server, port = constants.GERRIT_HOST, constants.GERRIT_PORT

    cmd = ['ssh', '-p', port, server, 'gerrit', 'query', '--current-patch-set',
           '--format=JSON', patch]

    result = cros_lib.RunCommand(cmd, redirect_stdout=True)
    result_dict = json.loads(result.output.splitlines()[0])
    if 'id' in result_dict:
      parsed_patches.append(GerritPatch(result_dict, internal))
    else:
      raise PatchException('Change-ID %s not found on server %s.'
                           % (patch, server))

  return parsed_patches


def _GetRemoteTrackingBranch(project_dir, branch):
  """Get the remote tracking branch of a local branch.

  Raises:
    cros_lib.NoTrackingBranchException if branch does not track anything.
  """
  (remote, ref) = cros_lib.GetTrackingBranch(branch, project_dir)
  return cros_lib.GetShortBranchName(remote, ref)


def _GetProjectManifestBranch(buildroot, project):
  """Get the branch specified in the manifest for the project."""
  (remote, ref) = cros_lib.GetProjectManifestBranch(buildroot,
                                                    project)
  return cros_lib.GetShortBranchName(remote, ref)


def PrepareLocalPatches(patches, manifest_branch):
  """Finish validation of parameters, and save patches to a temp folder.

  Args:
    patches:  A list of user-specified patches, in project[:branch] form.
    manifest_branch: The manifest branch of the buildroot.

  Raises:
    PatchException if:
      1. The project branch isn't specified and the project isn't on a branch.
      2. The project branch doesn't track a remote branch.
  """
  patch_info = []
  patch_root = tempfile.mkdtemp(prefix=_TRYBOT_TEMP_PREFIX)

  for id in range(0, len(patches)):
    project, branch = patches[id].split(':')
    project_dir = cros_lib.GetProjectDir('.', project)

    patch_dir = os.path.join(patch_root, str(id))
    cmd = ['git', 'format-patch', '%s..%s' % ('m/' + manifest_branch, branch),
           '-o', patch_dir]
    cros_lib.RunCommand(cmd, redirect_stdout=True, cwd=project_dir)
    if not len(os.listdir(patch_dir)):
      raise PatchException('No changes found in %s:%s' % (project, branch))

    # Store remote tracking branch for verification during patch stage.
    try:
      tracking_branch = _GetRemoteTrackingBranch(project_dir, branch)
    except cros_lib.NoTrackingBranchException:
      raise PatchException('%s:%s needs to track a remote branch!'
                           % (project,branch))

    patch_info.append(LocalPatch(project, tracking_branch, patch_dir, branch))

  return patch_info






