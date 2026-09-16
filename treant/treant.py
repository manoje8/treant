import csv
import hashlib
import io
import json
import logging
from pathlib import Path

from treant.constants import HTML_FORMATS, IMAGE_FORMATS, OFFICE_FORMATS, TEXT_FORMATS, ParseMethod
from treant.docling import DoclingParser
from treant.document_cache import DocumentCache
from treant.google_document_ai import GoogleDocAI
from treant.settings import settings

logger = logging.getLogger(__name__)


def get_parser_method(parser_type: str):
    """
    Factory function to instantiate the appropriate document parser.

    Args:
        parser_type: The type of parser to use.

    Returns:
        An instance of the requested parser or default parser.
    """
    parser_name = parser_type.strip().lower()

    if parser_name == ParseMethod.GOOGLE_DOC_AI:
        return GoogleDocAI()
    elif parser_name == ParseMethod.DOCLING:
        return DoclingParser()
    else:
        logger.warning(
            f"Unsupported parser type: {parser_type}. "
            f"Using default Docling parser"
            f"Available options: {ParseMethod.GOOGLE_DOC_AI}, {ParseMethod.DOCLING}"
        )
        return DoclingParser()


def hash_file_content(file_path: Path) -> str:
    """
    Return a BLAKE2b-256 hex digest of *file_path*'s full byte content.
    """
    hasher = hashlib.blake2b(digest_size=32)
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65_536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def generate_cache_key(file_path: Path, parse_method: str) -> str:
    """
    Build a cache key from the file's *content* hash + parse method.
    """
    content_hash = hash_file_content(file_path)

    config_dict = {
        "content_hash": content_hash,
        "parse_method": parse_method,
    }

    config_str = json.dumps(config_dict, sort_keys=True)
    return hashlib.sha256(config_str.encode()).hexdigest()


def generate_doc_id(file_path: str | Path, read_bytes: int = 8192) -> str:
    path = Path(file_path).resolve()
    stat = path.stat()
    hasher = hashlib.sha256()
    hasher.update(str(stat.st_size).encode())

    with open(path, "rb") as f:
        hasher.update(f.read(read_bytes))

    return hasher.hexdigest()[:24]


async def _collect(result: any) -> list[dict[str, any]]:
    """
    Normalize a parser method's return value into a list.

    Parser methods are being migrated one at a time to return async
    generators instead of plain lists. This accepts either shape so
    process_document keeps working mid-migration, regardless of which
    individual parse_* methods have been converted yet.
    """
    import inspect

    if inspect.isasyncgen(result):
        return [block async for block in result]
    if inspect.iscoroutine(result):
        result = await result
        return await _collect(result)  # re-normalize in case awaiting it
    if isinstance(result, list):
        return result
    raise TypeError(
        f"Unexpected parser return type: {type(result).__name__}. "
        "Expected list, coroutine, or async generator."
    )


async def process_document(
    file_path: str | Path,
    parse_method: ParseMethod = ParseMethod.DOCLING,
    output_path: Path | None = None,
    display_stats: bool = False,
    project_root: Path | None = None,
    cache: DocumentCache | None = None,
    output_format: str = "text",
    table_format: str = "pipe",
    **kwargs,
):
    """Process a document and extract its content.

    Args:
        file_path: Path to the input document.
        parse_method: The parsing method to use.
        output_path: Optional path to save extracted content.
        display_stats: Whether to display content statistics.
        project_root: Root of the project path, used to locate the
            on-disk cache.  Ignored when *cache* is provided explicitly.
        cache: An explicit: class:`DocumentCache` instance to use.  When
            ``None`` (default) a new instance is created from
            *project_root*.
        output_format: ``'text'`` (default) for plain-text output or
            ``'json'`` to serialise the full *content_list* as JSON.
        table_format: How to render table blocks on output — ``'pipe'``
            (default), ``'csv'``, or ``'markdown'``.
        **kwargs: Additional arguments passed to the parser.

    Returns:
        List of extracted content blocks.

    Raises:
        FileNotFoundError: If the input file doesn't exist.
        ValueError: If the file is too large, a format is unsupported, or parsing fails.
        ImportError: If required, parser dependencies are not installed.
    """
    import asyncio

    if cache is None:
        if project_root is None:
            project_root = Path.cwd().parent
            logger.debug(f"No project_root provided, using parent directory: {project_root}")
        cache = DocumentCache(project_root=project_root)
        logger.debug(f"Created DocumentCache at: {cache.cache_dir}")

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

    cache_key = generate_cache_key(file_path, parse_method)
    cache_result = cache.get(cache_key)

    if cache_result is not None:
        logger.info(f"Cache HIT - Returning cached result for {file_path}")
        doc_id = generate_doc_id(file_path)
        return cache_result, doc_id

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
            content_list = await _collect(
                doc_parser.parse_pdf(file_path=file_path, method=method_value, **kwargs)
            )
        elif ext in HTML_FORMATS:
            logger.info("Detected HTML file, extracting structured content...")
            content_list = await _collect(
                await asyncio.to_thread(
                    doc_parser.parse_html, file_path=file_path, method=method_value, **kwargs
                )
            )
        elif ext in OFFICE_FORMATS:
            logger.info("Detected Office document, extracting content...")
            content_list = await _collect(
                await asyncio.to_thread(
                    doc_parser.parse_doc, file_path=file_path, method=method_value, **kwargs
                )
            )
        elif ext in TEXT_FORMATS:
            logger.info("Detected text file, reading content...")
            content_list = await _collect(
                await asyncio.to_thread(doc_parser.parse_text_file, file_path=file_path, **kwargs)
            )
        elif ext in IMAGE_FORMATS:
            logger.info("Detected image file, extracting content via OCR...")
            content_list = await _collect(
                await asyncio.to_thread(
                    doc_parser.parse_image, file_path=file_path, method=method_value, **kwargs
                )
            )
        else:
            supported_formats = (
                [".pdf"]
                + list(OFFICE_FORMATS)
                + list(HTML_FORMATS)
                + list(TEXT_FORMATS)
                + list(IMAGE_FORMATS)
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

    cache.store(cache_key, content_list, file_path, parse_method=parse_method)
    doc_id = generate_doc_id(file_path)

    if display_stats:
        display_content_stats(content_list)

    if output_path:
        await save_content(
            content_list, output_path, output_format=output_format, table_format=table_format
        )

    return content_list, doc_id


def display_content_stats(content_list: list) -> None:
    """Display statistics about extracted content blocks."""
    logger.info("─" * 50)
    logger.info("Content Statistics:")
    logger.info(f"  • Total content blocks: {len(content_list)}")

    block_types: dict[str, int] = {}
    for block in content_list:
        if isinstance(block, dict):
            block_type = str(block.get("type", "Unknown"))
            block_types[block_type] = block_types.get(block_type, 0) + 1

    if block_types:
        logger.info("  • Block type distribution:")
        for block_type, count in sorted(block_types.items()):
            logger.info(f"    - {block_type}: {count}")

    total_chars = sum(
        len(str(block.get("text", ""))) for block in content_list if isinstance(block, dict)
    )
    if total_chars > 0:
        logger.info(f"  • Total extracted characters: {total_chars:,}")

    logger.info("─" * 50)


async def save_content(
    content_list: list,
    output_path: Path,
    output_format: str = "text",
    table_format: str = "pipe",
) -> None:
    """Save extracted content to a file.

    Args:
        content_list: The list of content blocks to save.
        output_path: Destination file path.
        output_format: ``'text'`` for plain-text or ``'json'`` for
            structured JSON output.
        table_format: Table serialisation style — ``'pipe'`` (default),
            ``'csv'``, or ``'markdown'``.
    """
    try:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if output_format == "json":
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(content_list, f, indent=2, ensure_ascii=False)
        else:
            with open(output_path, "w", encoding="utf-8") as f:
                for block in content_list:
                    if isinstance(block, dict):
                        if block.get("type") == "table":
                            rendered = _render_table(block, table_format)
                            if rendered:
                                f.write(rendered + "\n\n")
                        else:
                            text = block.get("text", "")
                            if text:
                                f.write(text + "\n\n")
                    elif isinstance(block, str):
                        f.write(block + "\n\n")

        logger.info(f"Content saved to: {output_path}")
    except Exception as e:
        logger.error(f"Failed to save output: {str(e)}")
        raise


def _render_table(block: dict, table_format: str) -> str:
    """Render a table block in the requested format.

    Args:
        block: A content block with ``type == 'table'`` and a
            ``table_body`` key containing row data.
        table_format: ``'pipe'``, ``'csv'``, or ``'markdown'``.

    Returns:
        The serialised table as a string.
    """
    table_body = block.get("table_body", [])
    if not table_body:
        return ""

    # table_body is a list of rows; each row is a list of cell dicts
    # with a "text" key, or plain strings.
    rows: list[list[str]] = []
    for row in table_body:
        if isinstance(row, list):
            cells = []
            for cell in row:
                if isinstance(cell, dict):
                    cells.append(str(cell.get("text", "")))
                else:
                    cells.append(str(cell))
            rows.append(cells)

    if not rows:
        return ""

    if table_format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        for row in rows:
            writer.writerow(row)
        return buf.getvalue().rstrip("\r\n")

    elif table_format == "markdown":
        lines = []
        # First row as header
        header = rows[0]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join("---" for _ in header) + " |")
        for row in rows[1:]:
            # Pad or trim to match header column count
            padded = row + [""] * (len(header) - len(row))
            lines.append("| " + " | ".join(padded[: len(header)]) + " |")
        return "\n".join(lines)

    else:
        # Default: pipe-delimited (original behaviour)
        lines = []
        for row in rows:
            lines.append(" | ".join(row))
        return "\n".join(lines)
