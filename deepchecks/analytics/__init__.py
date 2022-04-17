# ----------------------------------------------------------------------------
# Copyright (C) 2021-2022 Deepchecks (https://www.deepchecks.com)
#
# This file is part of Deepchecks.
# Deepchecks is distributed under the terms of the GNU Affero General
# Public License (version 3 or later).
# You should have received a copy of the GNU Affero General Public License
# along with Deepchecks.  If not, see <http://www.gnu.org/licenses/>.
# ----------------------------------------------------------------------------
#
"""
Module for anonymous telemetry.

No credentials, data, personal information or anything private is collected (and will never be).
"""

from .anonymous_telemetry import send_anonymous_event, send_anonymous_run_event

__all__ = ["send_anonymous_event", "send_anonymous_run_event"]
