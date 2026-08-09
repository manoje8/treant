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

from treant.constants import HTML_FORMATS, OFFICE_FORMATS, TEXT_FORMATS, ParseMethod
from treant.docling import DoclingParser
from treant.google_document_ai import GoogleDocAI
from treant.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_parser_method(parser_type: str):
    """Factory function to instantiate the appropriate document parser.

    Args:
        parser_type: The type of parser to use.

    Returns:
        An instance of the requested parser.

    Raises:
        ValueError: If the parser type is not supported.
    """
    parser_name = parser_type.strip().lower()

    if parser_name == ParseMethod.GOOGLE_DOC_AI:
        return GoogleDocAI()
    elif parser_name == ParseMethod.DOCLING:
        return DoclingParser()
    else:
        raise ValueError(
            f"Unsupported parser type: {parser_type}. "
            f"Available options: {ParseMethod.GOOGLE_DOC_AI}, {ParseMethod.DOCLING}"
        )


async def process_document(
    file_path: str | Path,
    parse_method: ParseMethod,
    output_path: Path | None = None,
    display_stats: bool = False,
    **kwargs,
):
    """Process a document and extract its content.

    Args:
        file_path: Path to the input document.
        parse_method: The parsing method to use.
        output_path: Optional path to save extracted content.
        display_stats: Whether to display content statistics.
        **kwargs: Additional arguments passed to the parser.

    Returns:
        List of extracted content blocks.

    Raises:
        FileNotFoundError: If the input file doesn't exist.
        ValueError: If the file is too large, format is unsupported, or parsing fails.
        ImportError: If required parser dependencies are not installed.
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    if not file_path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")

    file_size = file_path.stat().st_size
    if file_size > settings.MAX_UPLOAD_BYTES:
        max_mb = settings.MAX_UPLOAD_BYTES // 1024 // 1024
        file_mb = file_size // 1024 // 1024
        raise ValueError(f"File too large: {file_mb} MB. Maximum allowed size is {max_mb} MB")

    ext = file_path.suffix.lower()
    logger.info(f"Processing: {file_path.name} ({file_size / 1024:.1f} KB)")

    try:
        doc_parser = get_parser_method(parser_type=parse_method)

        if not doc_parser.check_installation():
            parser_name = parse_method if isinstance(parse_method, str) else parse_method.value
            raise ImportError(
                f"Required dependencies for '{parser_name}' parser are not installed. "
                f"Please install the necessary packages and try again."
            )

        method_value = parse_method if isinstance(parse_method, str) else parse_method.value

        if ext == ".pdf":
            logger.info("Detected PDF file, parsing with OCR and layout analysis...")
            content_list = await asyncio.to_thread(
                doc_parser.parse_pdf,
                file_path=file_path,
                method=method_value,
                **kwargs,
            )
        elif ext in HTML_FORMATS:
            logger.info("Detected HTML file, extracting structured content...")
            content_list = await asyncio.to_thread(
                doc_parser.parse_html,
                file_path=file_path,
                method=method_value,
                **kwargs,
            )
        elif ext in OFFICE_FORMATS:
            logger.info("Detected Office document, extracting content...")
            content_list = await asyncio.to_thread(
                doc_parser.parse_doc,
                file_path=file_path,
                method=method_value,
                **kwargs,
            )
        elif ext in TEXT_FORMATS:
            logger.info("Detected text file, reading content...")
            content_list = await asyncio.to_thread(
                doc_parser.parse_doc,
                file_path=file_path,
                method=method_value,
                **kwargs,
            )
        else:
            supported_formats = (
                [".pdf"] + list(OFFICE_FORMATS) + list(HTML_FORMATS) + list(TEXT_FORMATS)
            )
            raise ValueError(
                f"Unsupported file format: '{ext}'. "
                f"Supported formats: {', '.join(sorted(supported_formats))}"
            )

    except Exception as e:
        logger.error(f"Failed to parse document: {str(e)}")
        raise

    if not content_list:
        raise ValueError(
            f"No content extracted from '{file_path.name}'. "
            f"The file may be empty, corrupted, or in an unsupported format."
        )

    logger.info(f"Successfully extracted {len(content_list)} content blocks")

    if display_stats:
        _display_content_stats(content_list)

    # Save to output file if specified
    if output_path:
        await _save_content(content_list, output_path)

    return content_list


def _display_content_stats(content_list: list) -> None:
    """Display statistics about extracted content blocks.

    Args:
        content_list: List of extracted content blocks.
    """
    logger.info("─" * 50)
    logger.info("Content Statistics:")
    logger.info(f"  • Total content blocks: {len(content_list)}")

    # Count block types
    block_types: dict[str, int] = {}
    for block in content_list:
        if isinstance(block, dict):
            block_type = str(block.get("type", "Unknown"))
            block_types[block_type] = block_types.get(block_type, 0) + 1

    if block_types:
        logger.info("  • Block type distribution:")
        for block_type, count in sorted(block_types.items()):
            logger.info(f"    - {block_type}: {count}")

    # Estimate total text length
    total_chars = sum(
        len(str(block.get("text", ""))) for block in content_list if isinstance(block, dict)
    )
    if total_chars > 0:
        logger.info(f"  • Total extracted characters: {total_chars:,}")

    logger.info("─" * 50)


async def _save_content(content_list: list, output_path: Path) -> None:
    """Save extracted content to a file.

    Args:
        content_list: List of content blocks to save.
        output_path: Path where content will be saved.
    """
    try:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            for block in content_list:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if text:
                        f.write(text + "\n\n")
                elif isinstance(block, str):
                    f.write(block + "\n\n")

        logger.info(f"Content saved to: {output_path}")
    except Exception as e:
        logger.error(f"Failed to save output: {str(e)}")
        raise


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


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nOperation cancelled by user")
        sys.exit(0)
