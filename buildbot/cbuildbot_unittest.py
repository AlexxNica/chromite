#!/usr/bin/python

# Copyright (c) 2011 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unittests for build stages."""

import getpass
import mox
import os
import sys
import unittest

import constants
sys.path.append(constants.SOURCE_ROOT)
import chromite.buildbot.cbuildbot as cbuildbot
import chromite.buildbot.cbuildbot_config as config
import chromite.lib.cros_build_lib as cros_lib


class TestExitedException(Exception):
  """Exception used by sys.exit() mock to halt execution."""
  pass

class TestHaltedException(Exception):
  """Exception used by mocks to halt execution without indicating failure."""
  pass

class TestFailedException(Exception):
  """Exception used by mocks to halt execution and indicate failure."""
  pass

class RunBuildStagesTest(mox.MoxTestBase):

  def setUp(self):
    mox.MoxTestBase.setUp(self)
    # Always stub RunCommmand out as we use it in every method.
    self.bot_id = 'x86-generic-pre-flight-queue'
    self.build_config = config.config[self.bot_id]
    self.build_config['master'] = False
    self.build_config['important'] = False

    # Use the cbuildbot parser to create properties and populate default values.
    self.parser = cbuildbot._CreateParser()
    (self.options, _) = self.parser.parse_args(['-r', '.', '--buildbot',
                                                '--debug'])
    self.options.resume = False
    self.options.sync = False
    self.options.build = False
    self.options.uprev = False
    self.options.tests = False
    self.options.archive = False
    self.options.remote_test_status = False
    self.options.patches = None
    self.options.prebuilts = False

    self.mox.StubOutWithMock(cbuildbot, '_GetChromiteTrackingBranch')
    cbuildbot._GetChromiteTrackingBranch().AndReturn('master')

  def testChromeosOfficialSet(self):
    """Verify that CHROMEOS_OFFICIAL is set correctly."""

    self.build_config['chromeos_official'] = True


    # Clean up before
    if 'CHROMEOS_OFFICIAL' in os.environ:
      del os.environ['CHROMEOS_OFFICIAL']

    self.mox.ReplayAll()

    self.assertFalse('CHROMEOS_OFFICIAL' in os.environ)

    cbuildbot.RunBuildStages(self.bot_id,
                             self.options,
                             self.build_config)

    self.assertTrue('CHROMEOS_OFFICIAL' in os.environ)

    self.mox.VerifyAll()

    # Clean up after the test
    if 'CHROMEOS_OFFICIAL' in os.environ:
      del os.environ['CHROMEOS_OFFICIAL']

  def testChromeosOfficialNotSet(self):
    """Verify that CHROMEOS_OFFICIAL is not always set."""

    self.build_config['chromeos_official'] = False

    # Clean up before
    if 'CHROMEOS_OFFICIAL' in os.environ:
      del os.environ['CHROMEOS_OFFICIAL']

    self.mox.ReplayAll()

    self.assertFalse('CHROMEOS_OFFICIAL' in os.environ)

    cbuildbot.RunBuildStages(self.bot_id,
                             self.options,
                             self.build_config)

    self.assertFalse('CHROMEOS_OFFICIAL' in os.environ)

    self.mox.VerifyAll()

    # Clean up after the test
    if 'CHROMEOS_OFFICIAL' in os.environ:
      del os.environ['CHROMEOS_OFFICIAL']


class InterfaceTest(mox.MoxTestBase):

  _X86_PREFLIGHT = 'x86-generic-pre-flight-queue'
  _BUILD_ROOT = '/b/test_build1'
  def setUp(self):
    mox.MoxTestBase.setUp(self)
    self.parser = cbuildbot._CreateParser()

  def testDebugBuildBotSetByDefault(self):
    """Test that debug and buildbot flags are set by default."""
    args = ['-r', self._BUILD_ROOT, self._X86_PREFLIGHT]
    (options, args) = self.parser.parse_args(args=args)
    self.assertEquals(options.debug, True)
    self.assertEquals(options.buildbot, False)

  def testBuildBotOption(self):
    """Test that --buildbot option unsets debug flag."""
    args = ['-r', self._BUILD_ROOT, '--buildbot', self._X86_PREFLIGHT]
    (options, args) = self.parser.parse_args(args=args)
    self.assertEquals(options.debug, False)
    self.assertEquals(options.buildbot, True)

  def testBuildBotWithDebugOption(self):
    """Test that --debug option overrides --buildbot option."""
    args = ['-r', self._BUILD_ROOT, '--buildbot', '--debug',
            self._X86_PREFLIGHT]
    (options, args) = self.parser.parse_args(args=args)
    self.assertEquals(options.debug, True)
    self.assertEquals(options.buildbot, True)

  def testBuildBotWithoutProfileOption(self):
    """Test that no --profile option gets defaulted."""
    args = ['--buildbot', self._X86_PREFLIGHT]
    (options, args) = self.parser.parse_args(args=args)
    self.assertEquals(options.profile, None)

  def testBuildBotWithProfileOption(self):
    """Test that --profile option gets parsed."""
    args = ['--buildbot', '--profile', 'carp', self._X86_PREFLIGHT]
    (options, args) = self.parser.parse_args(args=args)
    self.assertEquals(options.profile, 'carp')

  def testValidateClobberUserDeclines_1(self):
    """Test case where user declines in prompt."""
    self.mox.StubOutWithMock(os.path, 'exists')
    self.mox.StubOutWithMock(cbuildbot, '_GetInput')
    self.mox.StubOutWithMock(sys, 'exit')

    os.path.exists(self._BUILD_ROOT).AndReturn(True)
    cbuildbot._GetInput(mox.IgnoreArg()).AndReturn('No')
    sys.exit(0)

    self.mox.ReplayAll()
    cbuildbot._ValidateClobber(self._BUILD_ROOT)
    self.mox.VerifyAll()

  def testValidateClobberUserDeclines_2(self):
    """Test case where user does not enter the full 'yes' pattern."""
    self.mox.StubOutWithMock(os.path, 'exists')
    self.mox.StubOutWithMock(cbuildbot, '_GetInput')
    self.mox.StubOutWithMock(sys, 'exit')

    os.path.exists(self._BUILD_ROOT).AndReturn(True)
    cbuildbot._GetInput(mox.IgnoreArg()).AndReturn('y')
    sys.exit(0)

    self.mox.ReplayAll()
    cbuildbot._ValidateClobber(self._BUILD_ROOT)
    self.mox.VerifyAll()

  def testValidateClobberProtectRunningChromite(self):
    """User should not be clobbering our own source."""
    self.mox.StubOutWithMock(cros_lib, 'Die')
    cwd = os.path.dirname(os.path.realpath(__file__))
    buildroot = os.path.dirname(cwd)
    cros_lib.Die(mox.IgnoreArg()).AndRaise(Exception)
    self.mox.ReplayAll()
    self.assertRaises(Exception, cbuildbot._ValidateClobber, buildroot)
    self.mox.VerifyAll()


class FullInterfaceTest(unittest.TestCase):
  """Tests that run the cbuildbot.main() function directly.

  Don't inherit from MoxTestBase since it runs VerifyAll() at the end of every
  test which we don't want.
  """
  _BUILD_ROOT = '/b/test_build1'

  def setUp(self):
    self.mox = mox.Mox()

    # Create the parser before we stub out os.path.exists() - which the parser
    # creation code actually uses.
    parser = cbuildbot._CreateParser()

    # Stub out all relevant methods regardless of whether they are called in the
    # specific test case.  We can do this because we don't run VerifyAll() at
    # the end of every test.
    self.mox.StubOutWithMock(parser, 'error')
    self.mox.StubOutWithMock(cbuildbot.os.path, 'exists')
    self.mox.StubOutWithMock(cbuildbot, '_CreateParser')
    self.mox.StubOutWithMock(sys, 'exit')
    self.mox.StubOutWithMock(cbuildbot, '_GetInput')
    self.mox.StubOutWithMock(cros_lib, 'FindRepoDir')
    self.mox.StubOutWithMock(cbuildbot, '_SetupRedirectOutputToFile')
    self.mox.StubOutWithMock(cbuildbot, 'RunBuildStages')
    self.mox.StubOutWithMock(cbuildbot.os.path, 'realpath')

    parser.error(mox.IgnoreArg()).InAnyOrder().AndRaise(TestExitedException())
    cbuildbot._CreateParser().InAnyOrder().AndReturn(parser)
    sys.exit(mox.IgnoreArg()).InAnyOrder().AndRaise(TestExitedException())
    cros_lib.FindRepoDir().InAnyOrder().AndReturn('/b/test_build1/.repo')
    (cbuildbot.os.path.realpath('/b/test_build1').InAnyOrder().
        AndReturn('/b/test_build1'))
    cbuildbot._SetupRedirectOutputToFile().InAnyOrder()
    cbuildbot.RunBuildStages(mox.IgnoreArg(), mox.IgnoreArg(),
                             mox.IgnoreArg()).InAnyOrder().AndReturn(True)

    self.external_marker = ('/b/trybot/.trybot')
    self.internal_marker = ('/b/trybot-internal/.trybot')

  def tearDown(self):
    self.mox.UnsetStubs()

  def testDontInferBuildrootForBuildBotRuns(self):
    """Test that we don't infer buildroot if run with --buildbot option."""
    self.mox.ReplayAll()
    self.assertRaises(TestExitedException, cbuildbot.main,
                      ['--buildbot', 'x86-generic-pre-flight-queue'])

  def testInferExternalBuildRoot(self):
    """Test that we default to correct buildroot for external config."""
    self.mox.StubOutWithMock(cbuildbot, '_ConfirmBuildRoot')
    (cbuildbot._ConfirmBuildRoot(mox.IgnoreArg()).InAnyOrder()
        .AndRaise(TestHaltedException()))
    os.path.exists(self.external_marker).InAnyOrder().AndReturn(False)

    self.mox.ReplayAll()
    self.assertRaises(TestHaltedException, cbuildbot.main,
                      ['x86-generic-pre-flight-queue'])

  def testInferInternalBuildRoot(self):
    """Test that we default to correct buildroot for internal config."""
    self.mox.StubOutWithMock(cbuildbot, '_ConfirmBuildRoot')
    (cbuildbot._ConfirmBuildRoot(mox.IgnoreArg()).InAnyOrder()
        .AndRaise(TestHaltedException()))
    os.path.exists(self.internal_marker).InAnyOrder().AndReturn(False)

    self.mox.ReplayAll()
    self.assertRaises(TestHaltedException, cbuildbot.main,
                      ['x86-mario-pre-flight-queue'])

  def testInferBuildRootPromptNo(self):
    """Test that a 'no' answer on the prompt halts execution."""
    os.path.exists(self.external_marker).InAnyOrder().AndReturn(False)
    cbuildbot._GetInput(mox.IgnoreArg()).InAnyOrder().AndReturn('no')

    self.mox.ReplayAll()
    self.assertRaises(TestExitedException, cbuildbot.main,
                      ['x86-generic-pre-flight-queue'])

  def testInferBuildRootExists(self):
    """Test that we don't prompt the user if buildroot already exists."""
    os.path.exists(self.external_marker).InAnyOrder().AndReturn(True)
    (cbuildbot._GetInput(mox.IgnoreArg()).InAnyOrder()
        .AndRaise(TestFailedException()))

    self.mox.ReplayAll()
    cbuildbot.main(['x86-generic-pre-flight-queue'])

  def testValidateClobberForClobberOption(self):
    """Test that we ask for clobber confirmation for trybot runs."""
    os.path.realpath(self._BUILD_ROOT).InAnyOrder().AndReturn(self._BUILD_ROOT)
    self.mox.StubOutWithMock(cbuildbot, '_ValidateClobber')
    cbuildbot._ValidateClobber(self._BUILD_ROOT)
    self.mox.ReplayAll()
    cbuildbot.main(['-r', self._BUILD_ROOT, '--clobber',
                    'x86-generic-pre-flight-queue'])

  def testNoClobberConfirmationForBuildBotBuilds(self):
    """Test that we don't ask for clobber confirmation for --buildbot runs."""
    os.path.realpath(self._BUILD_ROOT).InAnyOrder().AndReturn(self._BUILD_ROOT)
    self.mox.StubOutWithMock(cbuildbot, '_ValidateClobber')
    self.mox.ReplayAll()
    cbuildbot.main(['-r', self._BUILD_ROOT, '--clobber', '--buildbot',
                    'x86-generic-pre-flight-queue'])

if __name__ == '__main__':
  unittest.main()
