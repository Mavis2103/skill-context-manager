"""Skill Context Manager (SCM) — Context-aware skill selection for AI agents."""

from .db import init_schema

__version__ = "0.7.2"

# Initialize the shared database schema on first import
init_schema()
