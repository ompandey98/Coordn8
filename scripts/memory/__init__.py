"""Coordn8 user-level memory: typed, append-only entries in the user zone.

    from memory.store import Store      # with scripts/ on sys.path

See store.py for the API and docs/DATA_SAFETY.md for the storage guarantees.
"""
from __future__ import annotations

from .store import CONFIDENCE, TYPES, Store, project_scope, subject_key  # noqa: F401

__all__ = ["Store", "TYPES", "CONFIDENCE", "project_scope", "subject_key"]
