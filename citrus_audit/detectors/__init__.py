"""Scoped deterministic detector adapters used by ``review_sheet``."""
from .paperconan import CHECKS as PAPERCONAN_CHECKS, detect_table as detect_paperconan
from .geng import CHECKS as GENG_CHECKS, detect_specialists as detect_geng

__all__ = ["PAPERCONAN_CHECKS", "GENG_CHECKS", "detect_paperconan", "detect_geng"]
