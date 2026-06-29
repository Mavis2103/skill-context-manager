"""Security helpers — ported from graphify security.py patterns.

Path validation: prevents traversal outside known skill directories.
Name sanitization: strips control chars, caps length.
"""
from __future__ import annotations

import re
from pathlib import Path

from .indexer import SkillIndexer

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_NAME_LEN = 128

# Known skill roots (HOME-relative) that are legitimate scan targets.
_KNOWN_SKILL_ROOTS: frozenset[Path] = frozenset(
    [Path.home() / d for d in SkillIndexer.AGENT_SKILL_DIRS]
    + [Path.home() / ".scm"]
)

# Extra roots the user may have passed via env var (colon-separated).
_EXTRA_ROOTS_ENV = "SCM_ALLOWED_ROOTS"


def _allowed_roots() -> list[Path]:
    """Return the union of known roots + env-var overrides."""
    roots = list(_KNOWN_SKILL_ROOTS)
    import os
    extra = os.environ.get(_EXTRA_ROOTS_ENV, "")
    if extra:
        roots.extend(Path(p).expanduser().resolve() for p in extra.split(":") if p.strip())
    return roots


def validate_skill_dir(path: str | Path) -> Path:
    """Resolve path and verify it's inside an allowed skill directory.

    Raises ValueError if the path escapes allowed roots or doesn't exist.
    """
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise ValueError(f"Path does not exist: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"Path is not a directory: {resolved}")
    for root in _allowed_roots():
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise ValueError(
        f"Path {resolved} is not inside an allowed skill directory. "
        f"Set {_EXTRA_ROOTS_ENV}=<colon-separated paths> to add custom roots."
    )


def sanitize_name(name: str | None) -> str:
    """Strip control characters and cap length.

    Safe for embedding in JSON, SQL, and MCP tool responses.
    """
    if name is None:
        return ""
    name = _CONTROL_CHAR_RE.sub("", str(name))
    if len(name) > _MAX_NAME_LEN:
        name = name[:_MAX_NAME_LEN]
    return name
