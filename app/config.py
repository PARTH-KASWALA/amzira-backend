"""
DEPRECATED: This module was historically used for settings.
It is intentionally kept minimal to avoid accidental imports.

Use `from app.core.config import settings` instead.
"""

from app.core.config import settings  # re-export for backwards compatibility

__all__ = ["settings"]
