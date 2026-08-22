# -*- coding: utf-8 -*-
"""
File: emsomat/sparkplug/vendor/time_utils.py

Vendored from pysparkplug._time (Apache 2.0), see NOTICE.md.
"""

import time

__all__ = ["get_current_timestamp"]


def get_current_timestamp() -> int:
    """Returns current time in a Sparkplug B compliant format (ms since epoch)."""
    return int(time.time() * 1e3)
