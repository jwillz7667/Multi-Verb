"""Verbio engine — real-time AI moderator for multi-participant research sessions."""

from importlib import metadata

try:
    __version__: str = metadata.version("verbio-engine")
except metadata.PackageNotFoundError:  # pragma: no cover — editable install before sync.
    __version__ = "0.0.0"

__all__ = ["__version__"]
