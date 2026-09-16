"""
Plugin-based parser registry for treant.

Parsers can be registered in two ways:

1. **Entry points** - third-party packages declare a ``treant.parsers``
   entry point group in their ``pyproject.toml``::

       [project.entry-points."treant.parsers"]
       my_parser = "my_package.module:MyParserClass"

   The entry point *name* becomes the parser key (e.g. ``"my_parser"``),
   and the *value* must point to a class that subclasses
   :class:`treant.base_parser.Parser`.

2. **Programmatic registration** - call :func:`register` at import time::

       from treant.parser_registry import register
       register("my_parser", MyParserClass)

The :func:`get_parser` function resolves a parser name to a *new instance*
of the corresponding class, falling back to the default parser when the
name is unknown.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from treant.base_parser import Parser

logger = logging.getLogger(__name__)

# Default parser name used as fallback when an unknown name is requested.
DEFAULT_PARSER = "docling"

# Internal mapping of parser name -> parser class.
# Populated lazily on first access and by explicit `register()` calls.
_registry: dict[str, type[Parser]] = {}
_entry_points_loaded = False


def _load_entry_points() -> None:
    """Discover parsers declared via the ``treant.parsers`` entry-point group."""
    global _entry_points_loaded
    if _entry_points_loaded:
        return

    discovered = entry_points(group="treant.parsers")
    for ep in discovered:
        if ep.name in _registry:
            logger.debug(
                "Parser %r already registered programmatically; skipping entry-point from %s",
                ep.name,
                ep.value,
            )
            continue
        try:
            cls = ep.load()
            _registry[ep.name] = cls
            logger.debug("Loaded parser %r from entry-point %s", ep.name, ep.value)
        except Exception:
            logger.exception("Failed to load parser entry-point %r (%s)", ep.name, ep.value)

    _entry_points_loaded = True


def register(name: str, parser_class: type[Parser]) -> None:
    """Register a parser class under *name*.

    Programmatic registrations take priority over entry-point discovery,
    so calling this *before* :func:`get_parser` ensures the class is used
    even if an entry point with the same name exists.

    Args:
        name: Unique identifier for the parser (e.g. ``"docling"``).
        parser_class: A :class:`~treant.base_parser.Parser` subclass.

    Raises:
        ValueError: If *name* is already registered.
    """
    if name in _registry:
        raise ValueError(
            f"Parser {name!r} is already registered "
            f"(class={_registry[name].__qualname__}). "
            f"Use a different name or call unregister() first."
        )
    _registry[name] = parser_class
    logger.debug("Registered parser %r -> %s", name, parser_class.__qualname__)


def unregister(name: str) -> None:
    """Remove a previously registered parser.

    Args:
        name: The parser name to remove.

    Raises:
        KeyError: If *name* is not currently registered.
    """
    if name not in _registry:
        raise KeyError(f"Parser {name!r} is not registered.")
    del _registry[name]
    logger.debug("Unregistered parser %r", name)


def get_parser(name: str) -> Parser:
    """Instantiate and return a parser for the given *name*.

    Resolution order:

    1. Programmatically registered classes (via :func:`register`).
    2. Entry-point discovered classes (``treant.parsers`` group).
    3. Falls back to the default parser (``docling``) with a warning.

    Args:
        name: Parser identifier (case-insensitive, stripped).

    Returns:
        A new parser instance.
    """
    _load_entry_points()

    key = name.strip().lower()
    cls = _registry.get(key)

    if cls is None:
        available = ", ".join(sorted(_registry)) or "(none)"
        logger.warning(
            "Unknown parser %r. Falling back to %r. Available parsers: %s",
            name,
            DEFAULT_PARSER,
            available,
        )
        cls = _registry.get(DEFAULT_PARSER)
        if cls is None:
            raise RuntimeError(
                f"Default parser {DEFAULT_PARSER!r} is not registered. "
                f"Available parsers: {available}"
            )

    return cls()


def available_parsers() -> list[str]:
    """Return sorted names of all registered parsers."""
    _load_entry_points()
    return sorted(_registry)


def _reset() -> None:
    """Clear all registrations (for testing only)."""
    global _entry_points_loaded
    _registry.clear()
    _entry_points_loaded = False
