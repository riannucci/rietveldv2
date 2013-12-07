# Copyright 2011 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Exception classes."""

class FrameworkException(Exception):
  """Base class for all exceptions in framework."""


class Forbidden(FrameworkException):
  """Exception raised when a resource is off limits."""
  STATUS_CODE = 403
  MSG = "You do not have permission to %s."

  def __init__(self, message):
    super(Forbidden, self).__init__(self.MSG % message)


class NotFound(FrameworkException):
  """Exception raised when a resource is not found."""
  STATUS_CODE = 404
  MSG = "%s was not found."

  def __init__(self, message):
    super(NotFound, self).__init__(self.MSG % message)


class BadData(FrameworkException):
  """Exception raised when input data is malformed."""
  STATUS_CODE = 400


class SpecialActionException(FrameworkException):
  """Base class for all exceptions which have special handling in middleware."""


class NeedsLogin(SpecialActionException):
  """Exception raised when there is a protected resource which needs a user
  account in order to display."""


