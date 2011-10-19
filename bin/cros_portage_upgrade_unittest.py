#!/usr/bin/python

# Copyright (c) 2011 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unit tests for cros_portage_upgrade.py."""

import cStringIO
import exceptions
import optparse
import os
import re
import sys
import unittest

import mox

import cros_portage_upgrade as cpu
import parallel_emerge
import portage.package.ebuild.config as portcfg
import portage.tests.resolver.ResolverPlayground as respgnd

# Regex to find the character sequence to turn text red (used for errors).
ERROR_PREFIX = re.compile('^\033\[1;31m')

# Configuration for generating a temporary valid ebuild hierarchy.
# TODO(mtennant): Wrap this mechanism to create multiple overlays.
# ResolverPlayground sets up a default profile with ARCH=x86, so
# other architectures are irrelevant for now.
DEFAULT_ARCH = 'x86'
EBUILDS = {
  "dev-libs/A-1": {"RDEPEND" : "dev-libs/B"},
  "dev-libs/A-2": {"RDEPEND" : "dev-libs/B"},
  "dev-libs/B-1": {"RDEPEND" : "dev-libs/C"},
  "dev-libs/B-2": {"RDEPEND" : "dev-libs/C"},
  "dev-libs/C-1": {},
  "dev-libs/C-2": {},
  "dev-libs/D-1": {"RDEPEND": "!dev-libs/E"},
  "dev-libs/D-2": {},
  "dev-libs/D-3": {},
  "dev-libs/E-2": {"RDEPEND": "!dev-libs/D"},
  "dev-libs/E-3": {},

  "dev-libs/F-1": {"SLOT": "1"},
  "dev-libs/F-2": {"SLOT": "2"},

  "dev-apps/X-1": {
    "EAPI": "3",
    "SLOT": "0",
    "KEYWORDS": "amd64 arm x86",
    "RDEPEND": "=dev-libs/C-1",
    },
  "dev-apps/Y-2": {
    "EAPI": "3",
    "SLOT": "0",
    "KEYWORDS": "amd64 arm x86",
    "RDEPEND": "=dev-libs/C-2",
    },

  "chromeos-base/flimflam-0.0.1-r228": {
    "EAPI" : "2",
    "SLOT" : "0",
    "KEYWORDS" : "amd64 x86 arm",
    "RDEPEND" : ">=dev-libs/D-2",
    },
  "chromeos-base/flimflam-0.0.2-r123": {
    "EAPI" : "2",
    "SLOT" : "0",
    "KEYWORDS" : "~amd64 ~x86 ~arm",
    "RDEPEND" : ">=dev-libs/D-3",
    },
  "chromeos-base/libchrome-57098-r4": {
    "EAPI" : "2",
    "SLOT" : "0",
    "KEYWORDS" : "amd64 x86 arm",
    "RDEPEND" : ">=dev-libs/E-2",
    },
  "chromeos-base/libcros-1": {
    "EAPI" : "2",
    "SLOT" : "0",
    "KEYWORDS" : "amd64 x86 arm",
    "RDEPEND" : "dev-libs/B dev-libs/C chromeos-base/flimflam",
    "DEPEND" :
    "dev-libs/B dev-libs/C chromeos-base/flimflam chromeos-base/libchrome",
    },

  "virtual/libusb-0"         : {
    "EAPI" :"2", "SLOT" : "0",
    "RDEPEND" :
    "|| ( >=dev-libs/libusb-0.1.12-r1:0 dev-libs/libusb-compat " +
    ">=sys-freebsd/freebsd-lib-8.0[usb] )"},
  "virtual/libusb-1"         : {
    "EAPI" :"2", "SLOT" : "1",
    "RDEPEND" : ">=dev-libs/libusb-1.0.4:1"},
  "dev-libs/libusb-0.1.13"   : {},
  "dev-libs/libusb-1.0.5"    : {"SLOT":"1"},
  "dev-libs/libusb-compat-1" : {},
  "sys-freebsd/freebsd-lib-8": {"IUSE" : "+usb"},

  "sys-fs/udev-164"          : {"EAPI" : "1", "RDEPEND" : "virtual/libusb:0"},

  "virtual/jre-1.5.0"        : {
    "SLOT" : "1.5",
    "RDEPEND" : "|| ( =dev-java/sun-jre-bin-1.5.0* =virtual/jdk-1.5.0* )"},
  "virtual/jre-1.5.0-r1"     : {
    "SLOT" : "1.5",
    "RDEPEND" : "|| ( =dev-java/sun-jre-bin-1.5.0* =virtual/jdk-1.5.0* )"},
  "virtual/jre-1.6.0"        : {
    "SLOT" : "1.6",
    "RDEPEND" : "|| ( =dev-java/sun-jre-bin-1.6.0* =virtual/jdk-1.6.0* )"},
  "virtual/jre-1.6.0-r1"     : {
    "SLOT" : "1.6",
    "RDEPEND" : "|| ( =dev-java/sun-jre-bin-1.6.0* =virtual/jdk-1.6.0* )"},
  "virtual/jdk-1.5.0"        : {
    "SLOT" : "1.5",
    "RDEPEND" : "|| ( =dev-java/sun-jdk-1.5.0* dev-java/gcj-jdk )"},
  "virtual/jdk-1.5.0-r1"     : {
    "SLOT" : "1.5",
    "RDEPEND" : "|| ( =dev-java/sun-jdk-1.5.0* dev-java/gcj-jdk )"},
  "virtual/jdk-1.6.0"        : {
    "SLOT" : "1.6",
    "RDEPEND" : "|| ( =dev-java/icedtea-6* =dev-java/sun-jdk-1.6.0* )"},
  "virtual/jdk-1.6.0-r1"     : {
    "SLOT" : "1.6",
    "RDEPEND" : "|| ( =dev-java/icedtea-6* =dev-java/sun-jdk-1.6.0* )"},
  "dev-java/gcj-jdk-4.5"     : {},
  "dev-java/gcj-jdk-4.5-r1"  : {},
  "dev-java/icedtea-6.1"     : {},
  "dev-java/icedtea-6.1-r1"  : {},
  "dev-java/sun-jdk-1.5"     : {"SLOT" : "1.5"},
  "dev-java/sun-jdk-1.6"     : {"SLOT" : "1.6"},
  "dev-java/sun-jre-bin-1.5" : {"SLOT" : "1.5"},
  "dev-java/sun-jre-bin-1.6" : {"SLOT" : "1.6"},

  "dev-java/ant-core-1.8"   : {"DEPEND"  : ">=virtual/jdk-1.4"},
  "dev-db/hsqldb-1.8"       : {"RDEPEND" : ">=virtual/jre-1.6"},
  }

WORLD = [
  "dev-libs/A",
  "dev-libs/D",
  "virtual/jre",
  ]

INSTALLED = {
  "dev-libs/A-1": {},
  "dev-libs/B-1": {},
  "dev-libs/C-1": {},
  "dev-libs/D-1": {},

  "virtual/jre-1.5.0"       : {
    "SLOT" : "1.5",
    "RDEPEND" : "|| ( =virtual/jdk-1.5.0* =dev-java/sun-jre-bin-1.5.0* )"},
  "virtual/jre-1.6.0"       : {
    "SLOT" : "1.6",
    "RDEPEND" : "|| ( =virtual/jdk-1.6.0* =dev-java/sun-jre-bin-1.6.0* )"},
  "virtual/jdk-1.5.0"       : {
    "SLOT" : "1.5",
    "RDEPEND" : "|| ( =dev-java/sun-jdk-1.5.0* dev-java/gcj-jdk )"},
  "virtual/jdk-1.6.0"       : {
    "SLOT" : "1.6",
    "RDEPEND" : "|| ( =dev-java/icedtea-6* =dev-java/sun-jdk-1.6.0* )"},
  "dev-java/gcj-jdk-4.5"    : {},
  "dev-java/icedtea-6.1"    : {},

  "virtual/libusb-0"         : {
    "EAPI" :"2", "SLOT" : "0",
    "RDEPEND" :
    "|| ( >=dev-libs/libusb-0.1.12-r1:0 dev-libs/libusb-compat " +
    ">=sys-freebsd/freebsd-lib-8.0[usb] )"},
  }

# For verifying dependency graph results
GOLDEN_DEP_GRAPHS = {
  "dev-libs/A-2" : { "needs" : { "dev-libs/B-2" : "runtime" },
                     "action" : "merge" },
  "dev-libs/B-2" : { "needs" : { "dev-libs/C-2" : "runtime" } },
  "dev-libs/C-2" : { "needs" : { } },
  "dev-libs/D-2" : { "needs" : { } },
  "dev-libs/E-3" : { "needs" : { } },
  "chromeos-base/libcros-1" : { "needs" : {
    "dev-libs/B-2" : "runtime/buildtime",
    "dev-libs/C-2" : "runtime/buildtime",
    "chromeos-base/libchrome-57098-r4" : "buildtime",
    "chromeos-base/flimflam-0.0.1-r228" : "runtime/buildtime"
    } },
  "chromeos-base/flimflam-0.0.1-r228" : { "needs" : {
    "dev-libs/D-2" : "runtime"
    } },
  "chromeos-base/libchrome-57098-r4" : { "needs" : {
    "dev-libs/E-3" : "runtime"
    } },
  }

# For verifying dependency list results
GOLDEN_DEP_LISTS = {
  "dev-libs/A" : ['dev-libs/A-2', 'dev-libs/B-2', 'dev-libs/C-2'],
  "dev-libs/B" : ['dev-libs/B-2', 'dev-libs/C-2'],
  "dev-libs/C" : ['dev-libs/C-2'],
  "virtual/libusb" : ['virtual/libusb-1', 'dev-libs/libusb-1.0.5'],
  "chromeos-base/libcros" : ['chromeos-base/libcros-1',
                             'chromeos-base/libchrome-57098-r4',
                             'dev-libs/E-3',
                             'dev-libs/B-2',
                             'dev-libs/C-2',
                             'chromeos-base/flimflam-0.0.1-r228',
                             'dev-libs/D-2',
                             ],
  }


def _GetGoldenDepsList(pkg):
  """Retrieve the golden dependency list for |pkg| from GOLDEN_DEP_LISTS."""
  return GOLDEN_DEP_LISTS.get(pkg, None)


def _VerifyDepsGraph(deps_graph, pkg):
  """Verfication function for Mox to validate deps graph for |pkg|."""
  if deps_graph is None:
    print "Error: no dependency graph passed into _GetPreOrderDepGraph"
    return False

  if type(deps_graph) != dict:
    print "Error: dependency graph is expected to be a dict.  Instead: "
    print repr(deps_graph)
    return False

  validated = True

  # Verify size
  golden_deps_list = _GetGoldenDepsList(pkg)
  if golden_deps_list == None:
    print("Error: golden dependency list not configured for %s package" %
          (pkg))
    validated = False
  elif len(deps_graph) != len(golden_deps_list):
    print("Error: expected %d dependencies for %s package, not %d" %
          (len(golden_deps_list), pkg, len(deps_graph)))
    validated = False

  # Verify dependencies, by comparing them to GOLDEN_DEP_GRAPHS
  for p in deps_graph:
    golden_pkg_info = None
    try:
      golden_pkg_info = GOLDEN_DEP_GRAPHS[p]
    except KeyError:
      print("Error: golden dependency graph not configured for %s package" %
            (p))
      validated = False
      continue

    pkg_info = deps_graph[p]
    for key in golden_pkg_info:
      golden_value = golden_pkg_info[key]
      value = pkg_info[key]
      if not value == golden_value:
        print("Error: while verifying '%s' value for %s package,"
              " expected:\n%r\nBut instead found:\n%r"
              % (key, p, golden_value, value))
        validated = False

  if not validated:
    print("Error: dependency graph for %s is not as expected.  Instead:\n%r" %
          (pkg, deps_graph))

  return validated


def _GenDepsGraphVerifier(pkg):
  """Generate a graph verification function for the given package."""
  return lambda deps_graph: _VerifyDepsGraph(deps_graph, pkg)


def _IsErrorLine(line):
  """Return True if |line| has prefix associated with error output."""
  return ERROR_PREFIX.search(line)

def _SetUpEmerge(world=None):
  """Prepare the temporary ebuild playground and emerge variables.

  This leverages test code in existing Portage modules to create an ebuild
  hierarchy.  This can be a little slow."""

  # TODO(mtennant): Support multiple overlays?  This essentially
  # creates just a default overlay.
  # Also note that ResolverPlayground assumes ARCH=x86 for the
  # default profile it creates.
  if world is None:
    world = WORLD
  playground = respgnd.ResolverPlayground(ebuilds=EBUILDS,
                                          installed=INSTALLED,
                                          world=world)

  # Set all envvars needed by emerge, since --board is being skipped.
  eroot = playground.eroot
  if eroot[-1:] == '/':
    eroot = eroot[:-1]
  os.environ["PORTAGE_CONFIGROOT"] = eroot
  os.environ["ROOT"] = eroot
  os.environ["PORTDIR"] = "%s/usr/portage" % eroot

  return playground

def _GetPortageDBAPI(playground):
  portroot = playground.settings["ROOT"]
  porttree = playground.trees[portroot]['porttree']
  return porttree.dbapi

def _TearDownEmerge(playground):
  """Delete the temporary ebuild playground files."""
  try:
    playground.cleanup()
  except AttributeError:
    pass

# Use this to configure Upgrader using standard command
# line options and arguments.
def _ParseCmdArgs(cmdargs):
  """Returns (options, args) tuple."""
  parser = cpu._CreateOptParser()
  return parser.parse_args(args=cmdargs)

UpgraderSlotDefaults = {
    '_curr_arch':   DEFAULT_ARCH,
    '_curr_board':  'some_board',
    '_unstable_ok': False,
    '_verbose':     False,
    }
def _MockUpgrader(mox, cmdargs=None, **kwargs):
  """Set up a mocked Upgrader object with the given args."""
  upgrader = mox.CreateMock(cpu.Upgrader)

  for slot in cpu.Upgrader.__slots__:
    upgrader.__setattr__(slot, None)

  # Initialize with command line if given.
  if cmdargs:
    (options, args) = _ParseCmdArgs(cmdargs)
    cpu.Upgrader.__init__(upgrader, options, args)

  # Override Upgrader attributes if requested.
  for slot in cpu.Upgrader.__slots__:
    value = None
    if slot in kwargs:
      value = kwargs[slot]
    elif slot in UpgraderSlotDefaults:
      value = UpgraderSlotDefaults[slot]

    if value is not None:
      upgrader.__setattr__(slot, value)

  return upgrader


class EmergeableTest(mox.MoxTestBase):
  """Test Upgrader._AreEmergeable."""

  def setUp(self):
    mox.MoxTestBase.setUp(self)

  def _TestAreEmergeable(self, cpvlist, expect,
                         stable_only=True, debug=False,
                         world=None):
    """Test the Upgrader._AreEmergeable method.

    |cpvlist| and |stable_only| are passed to _AreEmergeable.
    |expect| is boolean, expected return value of _AreEmergeable
    |debug| requests that emerge output in _AreEmergeable be shown.
    |world| is list of lines to override default world contents.
    """

    cmdargs = ['--upgrade'] + cpvlist
    mocked_upgrader = _MockUpgrader(self.mox, cmdargs=cmdargs)
    playground = _SetUpEmerge(world=world)

    # Add test-specific mocks/stubs

    # Replay script
    envvars = cpu.Upgrader._GenPortageEnvvars(mocked_upgrader,
                                              mocked_upgrader._curr_arch,
                                              not stable_only)
    mocked_upgrader._GenPortageEnvvars(mocked_upgrader._curr_arch,
                                       not stable_only).AndReturn(envvars)
    mocked_upgrader._GetBoardCmd('emerge').AndReturn('emerge')
    self.mox.ReplayAll()

    # Verify
    result = cpu.Upgrader._AreEmergeable(mocked_upgrader, cpvlist, stable_only)
    self.mox.VerifyAll()

    (code, cmd, output) = result
    if debug or code != expect:
      print("\nTest ended with success==%r (expected==%r)" % (code, expect))
      print("Emerge output:\n%s" % output)

    self.assertEquals(code, expect)

    _TearDownEmerge(playground)

  def testAreEmergeableOnePkg(self):
    """Should pass, one cpv target."""
    cpvlist = ['dev-libs/A-1']
    return self._TestAreEmergeable(cpvlist, True)

  def testAreEmergeableTwoPkgs(self):
    """Should pass, two cpv targets."""
    cpvlist = ['dev-libs/A-1', 'dev-libs/B-1']
    return self._TestAreEmergeable(cpvlist, True)

  def testAreEmergeableOnePkgTwoVersions(self):
    """Should fail, targets two versions of same package."""
    cpvlist = ['dev-libs/A-1', 'dev-libs/A-2']
    return self._TestAreEmergeable(cpvlist, False)

  def testAreEmergeableStableFlimFlam(self):
    """Should pass, target stable version of pkg."""
    cpvlist = ['chromeos-base/flimflam-0.0.1-r228']
    return self._TestAreEmergeable(cpvlist, True)

  def testAreEmergeableUnstableFlimFlam(self):
    """Should fail, target unstable version of pkg."""
    cpvlist = ['chromeos-base/flimflam-0.0.2-r123']
    return self._TestAreEmergeable(cpvlist, False)

  def testAreEmergeableUnstableFlimFlamUnstableOk(self):
    """Should pass, target unstable version of pkg with stable_only=False."""
    cpvlist = ['chromeos-base/flimflam-0.0.2-r123']
    return self._TestAreEmergeable(cpvlist, True, stable_only=False)

  def testAreEmergeableBlockedPackages(self):
    """Should fail, targets have blocking deps on each other."""
    cpvlist = ['dev-libs/D-1', 'dev-libs/E-2']
    return self._TestAreEmergeable(cpvlist, False)

  def testAreEmergeableBlockedByInstalledPkg(self):
    """Should fail because of installed D-1 pkg."""
    cpvlist = ['dev-libs/E-2']
    return self._TestAreEmergeable(cpvlist, False)

  def testAreEmergeableNotBlockedByInstalledPkgNotInWorld(self):
    """Should pass because installed D-1 pkg not in world."""
    cpvlist = ['dev-libs/E-2']
    return self._TestAreEmergeable(cpvlist, True, world=[])

  def testAreEmergeableSamePkgDiffSlots(self):
    """Should pass, same package but different slots."""
    cpvlist = ['dev-libs/F-1', 'dev-libs/F-2']
    return self._TestAreEmergeable(cpvlist, True)

  def testAreEmergeableTwoPackagesIncompatibleDeps(self):
    """Should fail, targets depend on two versions of same pkg."""
    cpvlist = ['dev-apps/X-1', 'dev-apps/Y-2']
    return self._TestAreEmergeable(cpvlist, False)


####################
### UpgraderTest ###
####################

# TODO: This test class no longer works.  Replace its pieces one by one.

class UpgraderTest(mox.MoxTestBase):
  """Test the Upgrader class from cros_portage_upgrade."""

  def setUp(self):
    mox.MoxTestBase.setUp(self)

  # TODO(mtennant): Upgrader does not have a sense of _board anymore,
  # only for each call to runBoard.  Test setup must change.
  def _MockUpgrader(self, board='test_board', package='test_package',
                    verbose=False, rdeps=None, srcroot=None,
                    stable_repo=None, upstream_repo=None, csv_file=None):
    """Set up a mocked Upgrader object with the given args."""
    upgrader = self.mox.CreateMock(cpu.Upgrader)

    upgrader._args = [package]
    upgrader._curr_board = board
    upgrader._verbose = verbose
    upgrader._rdeps = rdeps
    upgrader._stable_repo = stable_repo
    upgrader._upstream_repo = upstream_repo
    upgrader._csv_file = csv_file

    return upgrader

  def _MockUpgraderOptions(self, board='test_board', package='test_package',
                           srcroot=None, upstream=None,
                           verbose=False, rdeps=None):
    """Mock optparse.Values for use with Upgrader, and create args list.

    Returns tuple with (options, args)."""

    if not srcroot:
      srcroot = '%s/trunk/src' % os.environ['HOME']

    options = self.mox.CreateMock(optparse.Values)

    # Make sure all attributes are initialized.
    for opt in cpu.Upgrader.OPT_SLOTS:
      setattr(options, opt, None)

    # Set the attributes we care about for testing.
    options.board = board
    options.verbose = verbose
    options.rdeps = rdeps
    options.srcroot = srcroot
    options.upstream = upstream
    args = [package]

    return (options, args)

  def _SetUpEmerge(self):
    """Prepare the temporary ebuild playground and emerge variables.

    This leverages test code in existing Portage modules to create an ebuild
    hierarchy.  This can be a little slow."""

    # TODO(mtennant): Support multiple overlays?  This essentially
    # creates just a default overlay.
    self._playground = respgnd.ResolverPlayground(ebuilds=EBUILDS,
                                                  installed=INSTALLED)

    # Set all envvars needed by emerge, since --board is being skipped.
    eroot = self._playground.eroot
    if eroot[-1:] == '/':
      eroot = eroot[:-1]
    os.environ["PORTAGE_CONFIGROOT"] = eroot
    os.environ["PORTAGE_SYSROOT"] = eroot
    os.environ["SYSROOT"] = eroot
    os.environ.setdefault("CHROMEOS_ROOT", "%s/trunk" % os.environ["HOME"])
    os.environ["PORTDIR"] = "%s/usr/portage" % eroot

  def _GetPortageDBAPI(self):
    portroot = self._playground.settings["ROOT"]
    porttree = self._playground.trees[portroot]['porttree']
    return porttree.dbapi

  def _TearDownEmerge(self):
    """Delete the temporary ebuild playground files."""
    try:
      self._playground.cleanup()
    except AttributeError:
      pass

  def _GetParallelEmergeArgv(self, mocked_upgrader):
    return cpu.Upgrader._GenParallelEmergeArgv(mocked_upgrader)

  #
  # _GetCurrentVersions testing
  #

  def _TestGetCurrentVersions(self, pkg):
    """Test the behavior of the Upgrader._GetCurrentVersions method.

    This basically confirms that it uses the parallel_emerge module to
    assemble the expected dependency graph."""
    mocked_upgrader = self._MockUpgrader(board=None, package=pkg, verbose=False)
    pm_argv = self._GetParallelEmergeArgv(mocked_upgrader)
    self._SetUpEmerge()

    # Add test-specific mocks/stubs.
    self.mox.StubOutWithMock(cpu.Upgrader, '_GetPreOrderDepGraph')

    # Replay script
    verifier = _GenDepsGraphVerifier(pkg)
    mocked_upgrader._GenParallelEmergeArgv().AndReturn(pm_argv)
    mocked_upgrader._SetPortTree(mox.IsA(portcfg.config), mox.IsA(dict))
    cpu.Upgrader._GetPreOrderDepGraph(mox.Func(verifier)).AndReturn(['ignore'])
    self.mox.ReplayAll()

    # Verify
    graph = cpu.Upgrader._GetCurrentVersions(mocked_upgrader)
    self.mox.VerifyAll()

    self._TearDownEmerge()

  def testGetCurrentVersionsDevLibsA(self):
    return self._TestGetCurrentVersions('dev-libs/A')

  def testGetCurrentVersionsDevLibsB(self):
    return self._TestGetCurrentVersions('dev-libs/B')

  def testGetCurrentVersionsCrosbaseLibcros(self):
    return self._TestGetCurrentVersions('chromeos-base/libcros')

  #
  # _GetPreOrderDepGraph testing
  #

  def _TestGetPreOrderDepGraph(self, pkg):
    """Test the behavior of the Upgrader._GetPreOrderDepGraph method."""

    mocked_upgrader = self._MockUpgrader(board=None, package=pkg, verbose=False)
    pm_argv = self._GetParallelEmergeArgv(mocked_upgrader)
    self._SetUpEmerge()

    # Replay script
    self.mox.ReplayAll()

    # Verify
    deps = parallel_emerge.DepGraphGenerator()
    deps.Initialize(pm_argv)
    deps_tree, deps_info = deps.GenDependencyTree()
    deps_graph = deps.GenDependencyGraph(deps_tree, deps_info)

    deps_list = cpu.Upgrader._GetPreOrderDepGraph(deps_graph)
    golden_deps_list = _GetGoldenDepsList(pkg)
    self.assertEquals(deps_list, golden_deps_list)
    self.mox.VerifyAll()

    self._TearDownEmerge()

  def testGetPreOrderDepGraphDevLibsA(self):
    return self._TestGetPreOrderDepGraph('dev-libs/A')

  def testGetPreOrderDepGraphDevLibsC(self):
    return self._TestGetPreOrderDepGraph('dev-libs/C')

  def testGetPreOrderDepGraphVirtualLibusb(self):
    return self._TestGetPreOrderDepGraph('virtual/libusb')

  def testGetPreOrderDepGraphCrosbaseLibcros(self):
    return self._TestGetPreOrderDepGraph('chromeos-base/libcros')

  #
  # _SplitEBuildPath testing
  #

  def _TestSplitEBuildPath(self, ebuild_path, golden_result):
    """Test the behavior of the Upgrader._SplitEBuildPath method."""
    mocked_upgrader = self._MockUpgrader()

    # Replay script
    self.mox.ReplayAll()

    # Verify
    result = cpu.Upgrader._SplitEBuildPath(mocked_upgrader,
                                           ebuild_path)
    self.assertEquals(result, golden_result)
    self.mox.VerifyAll()

  def testSplitEBuildPath1(self):
    return self._TestSplitEBuildPath('/foo/bar/portage/dev-libs/A/A-2.ebuild',
                                     ('portage', 'dev-libs', 'A', 'A-2'))

  def testSplitEBuildPath2(self):
    return self._TestSplitEBuildPath('/foo/ooo/ccc/ppp/ppp-1.2.3-r123.ebuild',
                                     ('ooo', 'ccc', 'ppp', 'ppp-1.2.3-r123'))


  #
  # _GetInfoListWithOverlays testing
  #

  def _TestGetInfoListWithOverlays(self, pkg):
    """Test the behavior of the Upgrader._GetInfoListWithOverlays method."""

    self._SetUpEmerge()

    # Add test-specific mocks/stubs

    # Replay script, if any
    self.mox.ReplayAll()

    # Verify
    cpvlist = _GetGoldenDepsList(pkg)
    (options, args) = self._MockUpgraderOptions(board=None,
                                                package=pkg,
                                                verbose=False)
    upgrader = cpu.Upgrader(options, args)
    upgrader._SetPortTree(self._playground.settings, self._playground.trees)

    cpvinfolist = upgrader._GetInfoListWithOverlays(cpvlist)
    self.mox.VerifyAll()

    # Verify the overlay that was found for each cpv.  Always "portage" for now,
    # because that is what is created by the temporary ebuild creator.
    # TODO(mtennant): Support multiple overlays somehow.
    for cpvinfo in cpvinfolist:
      self.assertEquals('portage', cpvinfo['overlay'])

    self._TearDownEmerge()

  def testGetInfoListWithOverlaysDevLibsA(self):
    return self._TestGetInfoListWithOverlays('dev-libs/A')

  def testGetInfoListWithOverlaysCrosbaseLibcros(self):
    return self._TestGetInfoListWithOverlays('chromeos-base/libcros')

  #
  # _UpgradePackages testing
  #
  # TODO(mtennant): Implement this.  It will require some cleverness.

  #
  # _ToCSV testing
  #
  # TODO(mtennant): Implement tests for CSV output functionality.

  #
  # _ToHTML testing
  #
  # TODO(mtennant): Implement tests for HTML output functionality.

################
### MainTest ###
################

class MainTest(mox.MoxTestBase):
  """Test argument handling at the main method level."""

  def setUp(self):
    """Setup for all tests in this class."""
    mox.MoxTestBase.setUp(self)

  def _StartCapturingOutput(self):
    """Begin capturing stdout and stderr."""
    self._stdout = sys.stdout
    self._stderr = sys.stderr
    sys.stdout = self._stdout_cap = cStringIO.StringIO()
    sys.stderr = self._stderr_cap = cStringIO.StringIO()

  def _RetrieveCapturedOutput(self):
    """Return captured output so far as (stdout, stderr) tuple."""
    try:
      return (self._stdout_cap.getvalue(), self._stderr_cap.getvalue())
    except AttributeError:
      # This will happen if output capturing isn't on.
      return None

  def _StopCapturingOutput(self):
    """Stop capturing stdout and stderr."""
    try:
      sys.stdout = self._stdout
      sys.stderr = self._stderr
    except AttributeError:
      # This will happen if output capturing wasn't on.
      pass

  def _PrepareArgv(self, *args):
    """Prepare command line for calling cros_portage_upgrade.main"""
    sys.argv = [ re.sub("_unittest", "", sys.argv[0]) ]
    sys.argv.extend(args)

  def _AssertOutputEndsInError(self, stdout):
    """Return True if |stdout| ends with an error message."""
    lastline = [ln for ln in stdout.split('\n') if ln][-1]
    self.assertTrue(_IsErrorLine(lastline),
                    msg="expected output to end in error line, but "
                    "_IsErrorLine says this line is not an error:\n%s" %
                    lastline)

  def _AssertCPUMain(self, cpu, expect_zero):
    """Run cpu.main() and assert exit value is expected.

    If |expect_zero| is True, assert exit value = 0.  If False,
    assert exit value != 0.
    """
    try:
      cpu.main()
    except exceptions.SystemExit, e:
      if expect_zero:
        self.assertEquals(e.args[0], 0,
                          msg="expected call to main() to exit cleanly, "
                          "but it exited with code %d" % e.args[0])
      else:
        self.assertNotEquals(e.args[0], 0,
                             msg="expected call to main() to exit with "
                             "failure code, but exited with code 0 instead.")

  def testHelp(self):
    """Test that --help is functioning"""
    self._PrepareArgv("--help")

    # Capture stdout/stderr so it can be verified later
    self._StartCapturingOutput()

    # Running with --help should exit with code==0
    try:
      cpu.main()
    except exceptions.SystemExit, e:
      self.assertEquals(e.args[0], 0)

    # Verify that a message beginning with "Usage: " was printed
    (stdout, stderr) = self._RetrieveCapturedOutput()
    self._StopCapturingOutput()
    self.assertTrue(stdout.startswith("Usage: "))

  def testMissingBoard(self):
    """Test that running without --board exits with an error."""
    self._PrepareArgv("")

    # Capture stdout/stderr so it can be verified later
    self._StartCapturingOutput()

    # Running without --board should exit with code!=0
    try:
      cpu.main()
    except exceptions.SystemExit, e:
      self.assertNotEquals(e.args[0], 0)

    (stdout, stderr) = self._RetrieveCapturedOutput()
    self._StopCapturingOutput()
    self._AssertOutputEndsInError(stdout)

  def testBoardWithoutPackage(self):
    """Test that running without a package argument exits with an error."""
    self._PrepareArgv("--board=any-board")

    # Capture stdout/stderr so it can be verified later
    self._StartCapturingOutput()

    # Running without a package should exit with code!=0
    self._AssertCPUMain(cpu, expect_zero=False)

    # Verify that an error message was printed.
    (stdout, stderr) = self._RetrieveCapturedOutput()
    self._StopCapturingOutput()
    self._AssertOutputEndsInError(stdout)

  def testHostWithoutPackage(self):
    """Test that running without a package argument exits with an error."""
    self._PrepareArgv("--host")

    # Capture stdout/stderr so it can be verified later
    self._StartCapturingOutput()

    # Running without a package should exit with code!=0
    self._AssertCPUMain(cpu, expect_zero=False)

    # Verify that an error message was printed.
    (stdout, stderr) = self._RetrieveCapturedOutput()
    self._StopCapturingOutput()
    self._AssertOutputEndsInError(stdout)

  def testFlowStatusReportOneBoard(self):
    """Test main flow for basic one-board status report."""
    self._StartCapturingOutput()

    self.mox.StubOutWithMock(cpu.Upgrader, 'PreRunChecks')
    self.mox.StubOutWithMock(cpu, '_BoardIsSetUp')
    self.mox.StubOutWithMock(cpu.Upgrader, 'PrepareToRun')
    self.mox.StubOutWithMock(cpu.Upgrader, 'RunBoard')
    self.mox.StubOutWithMock(cpu.Upgrader, 'RunCompleted')
    self.mox.StubOutWithMock(cpu.Upgrader, 'WriteTableFiles')

    cpu.Upgrader.PreRunChecks()
    cpu._BoardIsSetUp('any-board').AndReturn(True)
    cpu.Upgrader.PrepareToRun()
    cpu.Upgrader.RunBoard('any-board')
    cpu.Upgrader.RunCompleted()
    cpu.Upgrader.WriteTableFiles(csv='/dev/null')

    self.mox.ReplayAll()

    self._PrepareArgv("--board=any-board", "--to-csv=/dev/null", "any-package")
    self._AssertCPUMain(cpu, expect_zero=True)
    self.mox.VerifyAll()

    self._StopCapturingOutput()

  def testFlowStatusReportOneBoardNotSetUp(self):
    """Test main flow for basic one-board status report."""
    self._StartCapturingOutput()

    self.mox.StubOutWithMock(cpu.Upgrader, 'PreRunChecks')
    self.mox.StubOutWithMock(cpu, '_BoardIsSetUp')

    cpu.Upgrader.PreRunChecks()
    cpu._BoardIsSetUp('any-board').AndReturn(False)

    self.mox.ReplayAll()

    # Running with a package not set up should exit with code!=0
    self._PrepareArgv("--board=any-board", "--to-csv=/dev/null", "any-package")
    self._AssertCPUMain(cpu, expect_zero=False)
    self.mox.VerifyAll()

    # Verify that an error message was printed.
    (stdout, stderr) = self._RetrieveCapturedOutput()
    self._StopCapturingOutput()
    self._AssertOutputEndsInError(stdout)

  def testFlowStatusReportTwoBoards(self):
    """Test main flow for two-board status report."""
    self._StartCapturingOutput()

    self.mox.StubOutWithMock(cpu.Upgrader, 'PreRunChecks')
    self.mox.StubOutWithMock(cpu, '_BoardIsSetUp')
    self.mox.StubOutWithMock(cpu.Upgrader, 'PrepareToRun')
    self.mox.StubOutWithMock(cpu.Upgrader, 'RunBoard')
    self.mox.StubOutWithMock(cpu.Upgrader, 'RunCompleted')
    self.mox.StubOutWithMock(cpu.Upgrader, 'WriteTableFiles')

    cpu.Upgrader.PreRunChecks()
    cpu._BoardIsSetUp('board1').AndReturn(True)
    cpu._BoardIsSetUp('board2').AndReturn(True)
    cpu.Upgrader.PrepareToRun()
    cpu.Upgrader.RunBoard('board1')
    cpu.Upgrader.RunBoard('board2')
    cpu.Upgrader.RunCompleted()
    cpu.Upgrader.WriteTableFiles(csv=None)

    self.mox.ReplayAll()

    self._PrepareArgv("--board=board1:board2", "any-package")
    self._AssertCPUMain(cpu, expect_zero=True)
    self.mox.VerifyAll()

    self._StopCapturingOutput()

  def testFlowUpgradeOneBoard(self):
    """Test main flow for basic one-board upgrade."""
    self._StartCapturingOutput()

    self.mox.StubOutWithMock(cpu.Upgrader, 'PreRunChecks')
    self.mox.StubOutWithMock(cpu, '_BoardIsSetUp')
    self.mox.StubOutWithMock(cpu.Upgrader, 'PrepareToRun')
    self.mox.StubOutWithMock(cpu.Upgrader, 'RunBoard')
    self.mox.StubOutWithMock(cpu.Upgrader, 'RunCompleted')
    self.mox.StubOutWithMock(cpu.Upgrader, 'WriteTableFiles')

    cpu.Upgrader.PreRunChecks()
    cpu._BoardIsSetUp('any-board').AndReturn(True)
    cpu.Upgrader.PrepareToRun()
    cpu.Upgrader.RunBoard('any-board')
    cpu.Upgrader.RunCompleted()
    cpu.Upgrader.WriteTableFiles(csv=None)

    self.mox.ReplayAll()

    self._PrepareArgv("--upgrade", "--board=any-board", "any-package")
    self._AssertCPUMain(cpu, expect_zero=True)
    self.mox.VerifyAll()

    self._StopCapturingOutput()

  def testFlowUpgradeTwoBoards(self):
    """Test main flow for two-board upgrade."""
    self._StartCapturingOutput()

    self.mox.StubOutWithMock(cpu.Upgrader, 'PreRunChecks')
    self.mox.StubOutWithMock(cpu, '_BoardIsSetUp')
    self.mox.StubOutWithMock(cpu.Upgrader, 'PrepareToRun')
    self.mox.StubOutWithMock(cpu.Upgrader, 'RunBoard')
    self.mox.StubOutWithMock(cpu.Upgrader, 'RunCompleted')
    self.mox.StubOutWithMock(cpu.Upgrader, 'WriteTableFiles')

    cpu.Upgrader.PreRunChecks()
    cpu._BoardIsSetUp('board1').AndReturn(True)
    cpu._BoardIsSetUp('board2').AndReturn(True)
    cpu.Upgrader.PrepareToRun()
    cpu.Upgrader.RunBoard('board1')
    cpu.Upgrader.RunBoard('board2')
    cpu.Upgrader.RunCompleted()
    cpu.Upgrader.WriteTableFiles(csv='/dev/null')

    self.mox.ReplayAll()

    self._PrepareArgv("--upgrade", "--board=board1:board2",
                      "--to-csv=/dev/null", "any-package")
    self._AssertCPUMain(cpu, expect_zero=True)
    self.mox.VerifyAll()

    self._StopCapturingOutput()

  def testFlowUpgradeTwoBoardsAndHost(self):
    """Test main flow for two-board and host upgrade."""
    self._StartCapturingOutput()

    self.mox.StubOutWithMock(cpu.Upgrader, 'PreRunChecks')
    self.mox.StubOutWithMock(cpu, '_BoardIsSetUp')
    self.mox.StubOutWithMock(cpu.Upgrader, 'PrepareToRun')
    self.mox.StubOutWithMock(cpu.Upgrader, 'RunBoard')
    self.mox.StubOutWithMock(cpu.Upgrader, 'RunCompleted')
    self.mox.StubOutWithMock(cpu.Upgrader, 'WriteTableFiles')

    cpu.Upgrader.PreRunChecks()
    cpu._BoardIsSetUp('board1').AndReturn(True)
    cpu._BoardIsSetUp('board2').AndReturn(True)
    cpu.Upgrader.PrepareToRun()
    cpu.Upgrader.RunBoard(cpu.Upgrader.HOST_BOARD)
    cpu.Upgrader.RunBoard('board1')
    cpu.Upgrader.RunBoard('board2')
    cpu.Upgrader.RunCompleted()
    cpu.Upgrader.WriteTableFiles(csv='/dev/null')

    self.mox.ReplayAll()

    self._PrepareArgv("--upgrade", "--host", "--board=board1:host:board2",
                      "--to-csv=/dev/null", "any-package")
    self._AssertCPUMain(cpu, expect_zero=True)
    self.mox.VerifyAll()

    self._StopCapturingOutput()

if __name__ == '__main__':
  unittest.main()
