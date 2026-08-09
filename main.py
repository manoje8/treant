#!/usr/bin/env python3
"""
Document Parser CLI - Extract and process content from various document formats.

Supports PDF, Office documents, HTML, and text files with multiple parsing backends.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from treant.constants import ParseMethod
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
        description="📄 Document Parser - Extract content from various document formats",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s document.pdf
  %(prog)s document.docx --parse-method google_doc_ai
  %(prog)s document.html -o output.txt --stats
  %(prog)s document.pdf --parse-method docling --verbose
        """,
    )

    parser.add_argument("input_file", type=str, help="Path to the input document file")

    # Optional arguments
    parser.add_argument(
        "--parse-method",
        type=str,
        default=ParseMethod.DOCLING,
        choices=[ParseMethod.DOCLING, ParseMethod.GOOGLE_DOC_AI],
        help="Parsing backend to use (default: %(default)s)",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        metavar="PATH",
        help="Save extracted content to specified file",
    )

    parser.add_argument(
        "--stats", action="store_true", help="Display content extraction statistics"
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging output"
    )

    parser.add_argument("--version", action="version", version="%(prog)s 1.0.0")

    return parser


async def main() -> None:
    parser = setup_argument_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")

    try:
        await process_document(
            file_path=args.input_file,
            parse_method=args.parse_method,
            output_path=Path(args.output) if args.output else None,
            display_stats=args.stats,
        )

        logger.info("Document processing completed successfully!")

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
