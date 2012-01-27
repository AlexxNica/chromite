#!/usr/bin/python

# Copyright (c) 2011 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Library for interacting with gdata (i.e. Google Docs, Tracker, etc)."""

import functools
import getpass
import os
import re

import gdata.spreadsheet.service

import chromite.lib.operation as operation

# pylint: disable=W0201,E0203

CRED_FILE = os.path.join(os.environ['HOME'], '.gdata_cred.txt')

oper = operation.Operation('gdata_lib')

_BAD_COL_CHARS_REGEX = re.compile(r'[ /]')
def PrepColNameForSS(col):
  """Translate a column name for spreadsheet interface."""
  # Spreadsheet interface requires column names to be
  # all lowercase and with no spaces or other special characters.
  return _BAD_COL_CHARS_REGEX.sub('', col.lower())


# TODO(mtennant): Rename PrepRowValuesForSS
def PrepRowForSS(row):
  """Make sure spreadsheet handles all values in row as strings."""
  return dict((key, PrepValForSS(val)) for key, val in row.items())


# Regex to detect values that the spreadsheet will auto-format as numbers.
_NUM_REGEX = re.compile(r'^[\d\.]+$')
def PrepValForSS(val):
  """Make sure spreadsheet handles this value as a string."""
  if val and _NUM_REGEX.match(val):
    return "'" + val
  return val


def ScrubValFromSS(val):
  """Remove string indicator prefix if found."""
  if val and val[0] == "'":
    return val[1:]
  return val


class Creds(object):
  """Class to manage user/password credentials."""

  __slots__ = [
    'password',    # User password
    'user',        # User account (foo@chromium.org)
    ]

  def __init__(self, cred_file=None, user=None, password=None):
    # Prefer user/password if given.
    if user:
      if not user.endswith('@chromium.org'):
        user = '%s@chromium.org' % user

      if not password:
        password = getpass.getpass('Tracker password for %s:' % user)

      self.user = user
      self.password = password

    elif cred_file and os.path.exists(cred_file):
      self.LoadCreds(cred_file)

    else:
      raise TypeError('Invalid use of Creds - no user or credentials file')

  def LoadCreds(self, filepath):
    """Load email/password credentials from |filepath|."""
    # Read email from first line and password from second.

    with open(filepath, 'r') as f:
      (self.user, self.password) = (l.strip() for l in f.readlines())
    oper.Notice('Loaded Docs/Tracker login credentials from "%s"' % filepath)

  def StoreCreds(self, filepath):
    """Store email/password credentials to |filepath|."""
    oper.Notice('Storing Docs/Tracker login credentials to "%s"' % filepath)
    # Simply write email on first line and password on second.
    with open(filepath, 'w') as f:
      f.write(self.user + '\n')
      f.write(self.password + '\n')


class SpreadsheetRow(dict):
  """Minor semi-immutable extension of dict to keep the original spreadsheet
  row object and spreadsheet row number as attributes.

  No changes are made to equality checking or anything else, so client code
  that wishes to handle this as a pure dict can.
  """

  def __init__(self, ss_row_obj, ss_row_num, mapping=None):
    if mapping:
      dict.__init__(self, mapping)

    self.ss_row_obj = ss_row_obj
    self.ss_row_num = ss_row_num

  def __setitem__(self, key, val):
    raise TypeError('setting item in SpreadsheetRow not supported')

  def __delitem__(self, key):
    raise TypeError('deleting item in SpreadsheetRow not supported')


class SpreadsheetComm(object):
  """Class to manage communication with one Google Spreadsheet worksheet."""

  # Row numbering in spreadsheets effectively starts at 2 because row 1
  # has the column headers.
  ROW_NUMBER_OFFSET = 2

  # Spreadsheet column numbers start at 1.
  COLUMN_NUMBER_OFFSET = 1

  __slots__ = (
    '_columns',    # Tuple of translated column names, filled in as needed
    '_rows',       # Tuple of Row dicts in order, filled in as needed
    'gd_client',   # Google Data client
    'ss_key',      # Spreadsheet key
    'ws_name',     # Worksheet name
    'ws_key',      # Worksheet key
    )

  @property
  def columns(self):
    """The columns property is filled in on demand.

    It is a tuple of column names, each run through PrepColNameForSS.
    """
    if self._columns is None:
      cols = []

      query = gdata.spreadsheet.service.CellQuery()
      query['max-row'] = '1'
      feed = self.gd_client.GetCellsFeed(self.ss_key, self.ws_key, query=query)
      for entry in feed.entry:
        # The use of PrepColNameForSS here looks weird, but the values
        # in row 1 are the unaltered column names, rather than the restricted
        # column names used for interface purposes.  In other words, if the
        # spreadsheet looks like it has a column called "Foo Bar", then the
        # first row will have a value "Foo Bar" but all interaction with that
        # column for other rows will use column key "foobar".  Translate to
        # restricted names now with PrepColNameForSS.
        column = PrepColNameForSS(entry.content.text)
        cols.append(column)

      self._columns = tuple(cols)

    return self._columns

  @property
  def rows(self):
    """The rows property is filled in on demand.

    It is a tuple of SpreadsheetRow objects.
    """
    if self._rows is None:
      rows = []

      feed = self.gd_client.GetListFeed(self.ss_key, self.ws_key)
      for rowIx, rowObj in enumerate(feed.entry):
        row_dict = {}
        for key, val in rowObj.custom.items():
          row_dict[key] = ScrubValFromSS(val.text)
        row = SpreadsheetRow(rowObj, rowIx + self.ROW_NUMBER_OFFSET, row_dict)

        rows.append(row)

      self._rows = tuple(rows)

    return self._rows

  def __init__(self):
    for slot in self.__slots__:
      setattr(self, slot, None)

  def Connect(self, creds, ss_key, ws_name, source='chromiumos'):
    """Login to spreadsheet service and set current worksheet.

    |creds| Creds object (user/password) for Google Docs
    |ss_key| Spreadsheet key
    |ws_name| Worksheet name
    |source| Name to associate with connecting service
    """
    self._LoginWithUserPassword(creds.user, creds.password, source)
    self.SetCurrentWorksheet(ws_name, ss_key=ss_key)

  def SetCurrentWorksheet(self, ws_name, ss_key=None):
    """Change the current worksheet.  This clears all caches."""
    if ss_key and ss_key != self.ss_key:
      self.ss_key = ss_key
      self._ClearCache()

    self.ws_name = ws_name

    ws_key = self._GetWorksheetKey(self.ss_key, self.ws_name)
    if ws_key != self.ws_key:
      self.ws_key = ws_key
      self._ClearCache()

  def _ClearCache(self, keep_columns=False):
    """Called whenever column/row data might be stale."""
    self._rows = None
    if not keep_columns:
      self._columns = None

  def _LoginWithUserPassword(self, user, password, source):
    """Set up and connect the Google Doc client using email/password."""
    gd_client = RetrySpreadsheetsService()

    gd_client.source = source
    gd_client.email = user
    gd_client.password = password
    gd_client.ProgrammaticLogin()
    self.gd_client = gd_client

  def _GetWorksheetKey(self, ss_key, ws_name):
    """Get the worksheet key with name |ws_name| in spreadsheet |ss_key|."""
    feed = self.gd_client.GetWorksheetsFeed(ss_key)
    # The worksheet key is the last component in the URL (after last '/')
    for entry in feed.entry:
      if ws_name == entry.title.text:
        return entry.id.text.split('/')[-1]

    oper.Die('Unable to find worksheet "%s" in spreadsheet "%s"' %
             (ws_name, ss_key))

  def GetColumns(self):
    """Return tuple of column names in worksheet.

    Note that each returned name has been run through PrepColNameForSS.
    """
    return self.columns

  def GetColumnIndex(self, colName):
    """Get the column index (starting at 1) for column |colName|"""
    for ix, col in enumerate(self.columns):
      if colName == col:
        return ix + self.COLUMN_NUMBER_OFFSET

    return None

  def GetRows(self):
    """Return tuple of SpreadsheetRow objects in order."""
    return self.rows

  def GetRowCacheByCol(self, column):
    """Return a dict for looking up rows by value in |column|.

    Each row value is a SpreadsheetRow object.
    If more than one row has the same value for |column|, then the
    row objects will be in a list in the returned dict.
    """
    row_cache = {}

    for row in self.GetRows():
      col_val = row[column]

      current_entry = row_cache.get(col_val, None)
      if current_entry and type(current_entry) is list:
        current_entry.append(row)
      elif current_entry:
        current_entry = [current_entry, row]
      else:
        current_entry = row

      row_cache[col_val] = current_entry

    return row_cache

  def InsertRow(self, row):
    """Insert |row| at end of spreadsheet."""
    self.gd_client.InsertRow(row, self.ss_key, self.ws_key)
    self._ClearCache(keep_columns=True)

  def UpdateRowCellByCell(self, rowIx, row):
    """Replace cell values in row at |rowIx| with those in |row| dict."""
    for colName in row:
      colIx = self.GetColumnIndex(colName)
      if colIx is not None:
        self.ReplaceCellValue(rowIx, colIx, row[colName])
    self._ClearCache(keep_columns=True)

  def DeleteRow(self, ss_row):
    """Delete the given |ss_row| (must be original spreadsheet row object."""
    self.gd_client.DeleteRow(ss_row)
    self._ClearCache(keep_columns=True)

  def ReplaceCellValue(self, rowIx, colIx, val):
    """Replace cell value at |rowIx| and |colIx| with |val|"""
    self.gd_client.UpdateCell(rowIx, colIx, val, self.ss_key, self.ws_key)
    self._ClearCache(keep_columns=True)

  def ClearCellValue(self, rowIx, colIx):
    """Clear cell value at |rowIx| and |colIx|"""
    self.ReplaceCellValue(rowIx, colIx, None)


class RetrySpreadsheetsService(gdata.spreadsheet.service.SpreadsheetsService):
  """Extend SpreadsheetsService to put retry logic around http request method.

  The entire purpose of this class is to remove some flakiness from
  interactions with Google Docs spreadsheet service, in the form of
  certain 40* http error responses to http requests.  This is documented in
  http://code.google.com/p/chromium-os/issues/detail?id=23819.
  There are two "request" methods that need to be wrapped in retry logic.
  1) The request method on self.  Original implementation is in
     base class atom.service.AtomService.
  2) The request method on self.http_client.  The class of self.http_client
     can actually vary, so the original implementation of the request
     method can also vary.
  """
  # pylint: disable=R0904

  TRY_MAX = 5
  RETRYABLE_STATUSES = (403,)

  def __init__(self, *args, **kwargs):
    gdata.spreadsheet.service.SpreadsheetsService.__init__(self, *args,
                                                           **kwargs)

    # Wrap self.http_client.request with retry wrapper.  This request method
    # is used by ProgrammaticLogin(), at least.
    if hasattr(self, 'http_client'):
      self.http_client.request = functools.partial(self._RetryRequest,
                                                   self.http_client.request)

    self.request = functools.partial(self._RetryRequest, self.request)

  def _RetryRequest(self, func, *args, **kwargs):
    """Retry wrapper for bound |func|, passing |args| and |kwargs|.

    This retry wrapper can be used for any http request |func| that provides
    an http status code via the .status attribute of the returned value.

    Retry when the status value on the return object is in RETRYABLE_STATUSES,
    and run up to TRY_MAX times.  If successful (whether or not retries
    were necessary) return the last return value returned from base method.
    If unsuccessful return the first return value returned from base method.
    """
    first_retval = None
    for try_ix in xrange(1, self.TRY_MAX + 1):
      retval = func(*args, **kwargs)
      if retval.status not in self.RETRYABLE_STATUSES:
        return retval
      else:
        oper.Warning('Retry-able HTTP request failure (status=%d), try %d/%d' %
                     (retval.status, try_ix, self.TRY_MAX))
        if not first_retval:
          first_retval = retval

    oper.Warning('Giving up on HTTP request after %d tries' % self.TRY_MAX)
    return first_retval


# Support having this module test itself if run as __main__, by leveraging
# the gdata_lib_unittest module.
# Also, the unittests serve as extra documentation.
if __name__ == '__main__':
  import gdata_lib_unittest
  gdata_lib_unittest.unittest.main(gdata_lib_unittest)
