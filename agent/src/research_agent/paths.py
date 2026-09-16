"""Where the repo-level `config/` and `contracts/` directories live at runtime.

Both are mounted into the container at the image root, and both are plain files during local
development, so the lookup walks up from this module until it finds a directory that holds them.
`APP_CONFIG_DIR` / `APP_CONTRACTS_DIR` override the result (infrastructure only, see v0.6 §3).
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path

_MARKERS = ("config", "contracts")


class LayoutError(RuntimeError):
    """Raised when neither the environment nor the directory tree reveals the repo layout."""


@cache
def repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if all((candidate / marker).is_dir() for marker in _MARKERS):
            return candidate
    raise LayoutError(
        f"none of the parents of {__file__} contains both {' and '.join(_MARKERS)}; "
        "set APP_CONFIG_DIR and APP_CONTRACTS_DIR"
    )


def _resolve(env_var: str, marker: str) -> Path:
    override = os.environ.get(env_var)
    if override:
        path = Path(override).expanduser()
        if not path.is_dir():
            raise LayoutError(f"{env_var}={override} is not a directory")
        return path
    return repo_root() / marker


def config_dir() -> Path:
    """Directory holding `settings.yaml`, `gate.yaml`, `prompts/` and friends."""
    return _resolve("APP_CONFIG_DIR", "config")


def contracts_dir() -> Path:
    """Directory holding the cross-language contracts (`error_codes.yaml`, `run_states.yaml`)."""
    return _resolve("APP_CONTRACTS_DIR", "contracts")
