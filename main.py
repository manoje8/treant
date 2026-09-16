#!/usr/bin/env python3
"""
Document Parser CLI - Extract and process content from various document formats.

Supports PDF, Office documents, HTML, and text files with multiple parsing backends.
"""

import argparse
import asyncio
import json
import logging
import sys
from importlib.metadata import version as pkg_version
from pathlib import Path

from treant.constants import ParseMethod
from treant.document_cache import DocumentCache
from treant.treant import process_document

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def setup_argument_parser() -> argparse.ArgumentParser:
    """Configure and return the argument parser for the CLI.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        description="Document Parser - Extract content from various document formats",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s parse document.pdf
  %(prog)s parse document.docx --parse-method google_doc_ai
  %(prog)s parse document.html -o output.txt --stats
  %(prog)s parse document.pdf --parse-method docling --verbose
  %(prog)s cache stats
  %(prog)s cache clear
  %(prog)s cache invalidate /path/to/file.pdf
        """,
    )

    # Global options (before subcommand)
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging output"
    )
    parser.add_argument(
        "--project-root",
        type=str,
        default=None,
        metavar="PATH",
        help="Root directory for the on-disk cache (default: current working directory)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # parse (default document-processing command)
    parse_parser = subparsers.add_parser("parse", help="Parse a document and extract its content")
    parse_parser.add_argument("input_file", type=str, help="Path to the input document file")
    parse_parser.add_argument(
        "--parse-method",
        type=str,
        default=ParseMethod.DOCLING,
        choices=[ParseMethod.DOCLING, ParseMethod.GOOGLE_DOC_AI],
        help="Parsing backend to use (default: %(default)s)",
    )
    parse_parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        metavar="PATH",
        help="Save extracted content to specified file",
    )
    parse_parser.add_argument(
        "--stats", action="store_true", help="Display content extraction statistics"
    )
    parse_parser.add_argument(
        "--output-format",
        type=str,
        default="text",
        choices=["text", "json"],
        help="Output format: plain text or structured JSON (default: %(default)s)",
    )
    parse_parser.add_argument(
        "--table-format",
        type=str,
        default="pipe",
        choices=["pipe", "csv", "markdown"],
        help="Table serialisation style: pipe-delimited, CSV, or Markdown (default: %(default)s)",
    )

    # cache management subcommand
    cache_parser = subparsers.add_parser("cache", help="Manage the document cache")
    cache_sub = cache_parser.add_subparsers(dest="cache_action", help="Cache actions")

    cache_sub.add_parser("stats", help="Show cache statistics")
    cache_sub.add_parser("clear", help="Remove all cached entries")

    inv_parser = cache_sub.add_parser(
        "invalidate", help="Invalidate cache entries for a specific source file"
    )
    inv_parser.add_argument(
        "file", type=str, help="Path to the source file whose cache entries should be removed"
    )

    # backwards-compat: bare positional file arg (no subcommand)
    # Handled in main() by detecting when `command` is None and a
    # positional looks like a file path.

    try:
        _version = pkg_version("treant")
    except Exception:
        _version = "0.2.0"
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version}")

    return parser


def _resolve_project_root(raw: str | None) -> Path:
    return Path(raw) if raw else Path.cwd()


def _handle_cache(args: argparse.Namespace) -> None:
    """Dispatch ``treant cache {stats,clear,invalidate}``."""
    project_root = _resolve_project_root(args.project_root)
    cache = DocumentCache(project_root=project_root)

    if args.cache_action == "stats":
        info = cache.stats()
        print(json.dumps(info, indent=2))

    elif args.cache_action == "clear":
        removed = cache.clear()
        print(f"Cleared {removed} cache entries.")

    elif args.cache_action == "invalidate":
        file_path = Path(args.file).resolve()
        removed = cache.invalidate(str(file_path))
        if removed:
            print(f"Invalidated {removed} cache entries for {file_path}.")
        else:
            print(f"No cache entries found for {file_path}.")

    else:
        print("Usage: treant cache {stats,clear,invalidate}", file=sys.stderr)
        sys.exit(1)


async def _handle_parse(args: argparse.Namespace) -> None:
    """Run the document-parsing pipeline."""
    project_root = _resolve_project_root(args.project_root)
    logger.debug(f"Cache root: {project_root}")

    await process_document(
        file_path=args.input_file,
        parse_method=args.parse_method,
        output_path=Path(args.output) if args.output else None,
        display_stats=args.stats,
        project_root=project_root,
        output_format=args.output_format,
        table_format=args.table_format,
    )
    logger.info("Document processing completed successfully!")


async def main() -> None:
    parser = setup_argument_parser()

    # Backwards compatibility: bare file path without subcommand
    # If the first positional arg is not a known subcommand, treat it as
    # ``treant parse <file>`` so existing scripts keep working.
    known_commands = {"parse", "cache"}
    raw_args = sys.argv[1:]
    if raw_args and raw_args[0] not in known_commands and not raw_args[0].startswith("-"):
        raw_args = ["parse"] + raw_args

    args = parser.parse_args(raw_args)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")

    try:
        if args.command == "cache":
            _handle_cache(args)

        elif args.command == "parse":
            await _handle_parse(args)

        else:
            parser.print_help()
            sys.exit(1)

    except FileNotFoundError as e:
        logger.error(f"{str(e)}")
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Validation error: {str(e)}")
        sys.exit(1)
    except ImportError as e:
        logger.error(f"Dependency error: {str(e)}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        if args.verbose:
            logger.exception("Detailed traceback:")
        sys.exit(1)


def cli() -> None:
    """Sync entry point for the `treant` console script (see [project.scripts])."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nOperation cancelled by user")
        sys.exit(0)


if __name__ == "__main__":
    cli()
