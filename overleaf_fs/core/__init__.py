"""
Core package for Overleaf File System.

This module exposes a small, stable facade over core functionality so
that callers can import high-level helpers from ``overleaf_fs.core``
without needing to know the internal module layout.
"""

from .config import set_profile_root_dir, get_profile_root_dir_optional

__all__ = [
    "set_profile_root_dir",
    "get_profile_root_dir_optional"
]
