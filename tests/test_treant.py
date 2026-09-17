import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from treant.base_parser import Parser
from treant.constants import ParseMethod
from treant.parser_registry import _reset, available_parsers, get_parser, register
from treant.treant import (
    _render_table,
    display_content_stats,
    get_parser_method,
    process_document,
    save_content,
)


@pytest.fixture
def mock_parser():
    parser = MagicMock()
    parser.check_installation.return_value = True
    parser.parse_pdf.return_value = [{"type": "text", "text": "pdf content"}]
    parser.parse_html.return_value = [{"type": "text", "text": "html content"}]
    parser.parse_doc.return_value = [{"type": "text", "text": "doc content"}]
    parser.parse_image.return_value = [{"type": "text", "text": "image ocr content"}]
    return parser


@pytest.fixture(autouse=True)
def mock_cache():
    """Patch DocumentCache so no real filesystem cache is used in unit tests."""
    with (
        patch("treant.treant.DocumentCache.get", new_callable=AsyncMock, return_value=None),
        patch("treant.treant.DocumentCache.store", new_callable=AsyncMock),
    ):
        yield


def test_get_parser_method_docling():
    """Built-in 'docling' entry point should resolve to DoclingParser."""
    from treant.docling import DoclingParser

    parser = get_parser_method("docling")
    assert isinstance(parser, DoclingParser)


def test_get_parser_method_google():
    """Built-in 'google_doc_ai' entry point should resolve to GoogleDocAI."""
    from treant.google_document_ai import GoogleDocAI

    parser = get_parser_method("google_doc_ai")
    assert isinstance(parser, GoogleDocAI)


def test_get_parser_default_method():
    """Unknown parser names should fall back to the default (docling)."""
    from treant.docling import DoclingParser

    parser = get_parser_method("invalid")
    assert isinstance(parser, DoclingParser)


# Plugin registry tests


class _StubParser(Parser):
    """Minimal concrete parser for testing registration."""

    def check_installation(self) -> bool:
        return True


class TestParserRegistry:
    """Tests for the treant.parser_registry module."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        """Reset the registry before each test and restore after."""
        _reset()
        # Re-load built-in entry points so other tests aren't affected.
        yield
        _reset()

    def test_register_and_resolve(self):
        register("stub", _StubParser)
        parser = get_parser("stub")
        assert isinstance(parser, _StubParser)

    def test_duplicate_register_raises(self):
        register("stub", _StubParser)
        with pytest.raises(ValueError, match="already registered"):
            register("stub", _StubParser)

    def test_available_parsers_includes_registered(self):
        register("aaa_stub", _StubParser)
        names = available_parsers()
        assert "aaa_stub" in names

    def test_fallback_when_default_registered(self):
        """When 'docling' is registered, unknown names fall back to it."""
        register("docling", _StubParser)
        parser = get_parser("no_such_parser")
        assert isinstance(parser, _StubParser)

    def test_fallback_when_default_missing_raises(self):
        """When even the default parser is absent, RuntimeError is raised."""
        # Registry is empty after _reset(); also suppress entry-point discovery
        # so the built-in parsers aren't re-loaded.
        with patch("treant.parser_registry.entry_points", return_value=[]):
            with pytest.raises(RuntimeError, match="Default parser"):
                get_parser("no_such_parser")

    def test_case_insensitive_lookup(self):
        register("my_parser", _StubParser)
        parser = get_parser("  My_Parser  ")
        assert isinstance(parser, _StubParser)

    def test_entry_points_load_builtin_parsers(self):
        """Entry-point discovery should pick up the built-in parsers."""
        names = available_parsers()
        assert "docling" in names
        assert "google_doc_ai" in names

    def test_programmatic_takes_priority_over_entry_point(self):
        """A programmatic registration should shadow an entry-point parser."""
        register("docling", _StubParser)
        # Force re-discovery; the programmatic one should stick.
        parser = get_parser("docling")
        assert isinstance(parser, _StubParser)


@pytest.mark.asyncio
async def test_process_document_pdf(mock_parser, tmp_path):
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_text("dummy")

    with patch("treant.treant.get_parser_method", return_value=mock_parser):
        result, _ = await process_document(pdf_file, ParseMethod.DOCLING)

    assert result == [{"type": "text", "text": "pdf content"}]
    mock_parser.parse_pdf.assert_called_once_with(file_path=pdf_file, method="docling")


@pytest.mark.asyncio
async def test_process_document_html(mock_parser, tmp_path):
    html_file = tmp_path / "test.html"
    html_file.write_text("dummy")

    with patch("treant.treant.get_parser_method", return_value=mock_parser):
        result, _ = await process_document(html_file, ParseMethod.DOCLING)

    assert result == [{"type": "text", "text": "html content"}]
    mock_parser.parse_html.assert_called_once_with(file_path=html_file, method="docling")


@pytest.mark.asyncio
async def test_process_document_office(mock_parser, tmp_path):
    doc_file = tmp_path / "test.docx"
    doc_file.write_text("dummy")

    with patch("treant.treant.get_parser_method", return_value=mock_parser):
        result, _ = await process_document(doc_file, ParseMethod.DOCLING)

    assert result == [{"type": "text", "text": "doc content"}]
    mock_parser.parse_doc.assert_called_once_with(file_path=doc_file, method="docling")


@pytest.mark.asyncio
async def test_process_document_unsupported(mock_parser, tmp_path):
    invalid_file = tmp_path / "test.xyz"
    invalid_file.write_text("dummy")

    with patch("treant.treant.get_parser_method", return_value=mock_parser):
        with pytest.raises(ValueError, match="Unsupported file format"):
            await process_document(invalid_file, ParseMethod.DOCLING)


@pytest.mark.asyncio
async def test_process_document_file_too_large(mock_parser, tmp_path):
    large_file = tmp_path / "large.pdf"
    large_file.write_text("dummy" * 1000)

    with patch("treant.treant.settings.MAX_UPLOAD_BYTES", 1):
        with pytest.raises(ValueError, match="File too large"):
            await process_document(large_file, ParseMethod.DOCLING)


@pytest.mark.asyncio
async def test_process_document_missing_dependencies(mock_parser, tmp_path):
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_text("dummy")
    mock_parser.check_installation.return_value = False

    with patch("treant.treant.get_parser_method", return_value=mock_parser):
        with pytest.raises(ImportError, match="Required dependencies .* are not installed"):
            await process_document(pdf_file, ParseMethod.DOCLING)


@pytest.mark.asyncio
async def test_process_document_empty_content(mock_parser, tmp_path):
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_text("dummy")
    mock_parser.parse_pdf.return_value = []

    with patch("treant.treant.get_parser_method", return_value=mock_parser):
        with pytest.raises(ValueError, match="No content extracted"):
            await process_document(pdf_file, ParseMethod.DOCLING)


@pytest.mark.asyncio
async def test_save_content(tmp_path):
    content = [{"type": "text", "text": "Hello"}, {"type": "image", "text": ""}, "World"]
    output_file = tmp_path / "output.txt"

    await save_content(content, output_file)

    saved_text = output_file.read_text()
    assert "Hello\n\n" in saved_text
    assert "World\n\n" in saved_text


def test_display_content_stats(caplog):
    import logging

    caplog.set_level(logging.INFO)
    content = [{"type": "text", "text": "Hello"}, {"type": "image", "text": ""}, "World"]

    display_content_stats(content)

    assert "Total content blocks: 3" in caplog.text
    assert "text: 1" in caplog.text
    assert "image: 1" in caplog.text


# Image extraction routing
@pytest.mark.asyncio
async def test_process_document_image(mock_parser, tmp_path):
    """Image files (.png) should be routed to parse_image."""
    img_file = tmp_path / "photo.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    with patch("treant.treant.get_parser_method", return_value=mock_parser):
        result, _ = await process_document(img_file, ParseMethod.DOCLING)

    assert result == [{"type": "text", "text": "image ocr content"}]
    mock_parser.parse_image.assert_called_once_with(file_path=img_file, method="docling")


@pytest.mark.asyncio
async def test_process_document_image_jpg(mock_parser, tmp_path):
    """Image files (.jpg) should be routed to parse_image."""
    img_file = tmp_path / "photo.jpg"
    img_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

    with patch("treant.treant.get_parser_method", return_value=mock_parser):
        result, _ = await process_document(img_file, ParseMethod.DOCLING)

    assert result == [{"type": "text", "text": "image ocr content"}]
    mock_parser.parse_image.assert_called_once()


# Structured JSON output
@pytest.mark.asyncio
async def test_save_content_json(tmp_path):
    """--output-format json should produce valid JSON with the full content_list."""
    content = [
        {"type": "text", "text": "Hello"},
        {"type": "table", "table_body": [[{"text": "A"}, {"text": "B"}]]},
    ]
    output_file = tmp_path / "output.json"

    await save_content(content, output_file, output_format="json")

    loaded = json.loads(output_file.read_text())
    assert loaded == content


@pytest.mark.asyncio
async def test_save_content_text_default(tmp_path):
    """Default text output still works as before."""
    content = [{"type": "text", "text": "Hello"}, "World"]
    output_file = tmp_path / "output.txt"

    await save_content(content, output_file)

    saved_text = output_file.read_text()
    assert "Hello\n\n" in saved_text
    assert "World\n\n" in saved_text


# Equation rendering – LaTeX detection
class TestLatexDetection:
    """Verify _detect_latex on the DoclingParser."""

    @staticmethod
    def _detect(text: str) -> str:
        from treant.docling import DoclingParser

        return DoclingParser._detect_latex(text)

    def test_latex_frac(self):
        assert self._detect(r"\frac{a}{b}") == "latex"

    def test_latex_begin_env(self):
        assert self._detect(r"\begin{equation}x^2\end{equation}") == "latex"

    def test_latex_inline_math(self):
        assert self._detect(r"$E = mc^2$") == "latex"

    def test_latex_display_math(self):
        assert self._detect(r"$$\sum_{i=1}^{n} x_i$$") == "latex"

    def test_latex_sqrt(self):
        assert self._detect(r"\sqrt{2}") == "latex"

    def test_latex_subscript_braces(self):
        assert self._detect(r"x_{i+1}") == "latex"

    def test_latex_superscript_braces(self):
        assert self._detect(r"e^{i\pi}") == "latex"

    def test_plain_numeric(self):
        assert self._detect("3.14159") == "plain"

    def test_plain_text(self):
        assert self._detect("no latex here") == "plain"

    def test_plain_simple_equation(self):
        assert self._detect("a + b = c") == "plain"


# Table output as CSV / Markdown
class TestRenderTable:
    """Verify _render_table for all three table formats."""

    SAMPLE_BLOCK = {
        "type": "table",
        "table_body": [
            [{"text": "Name"}, {"text": "Age"}],
            [{"text": "Alice"}, {"text": "30"}],
            [{"text": "Bob"}, {"text": "25"}],
        ],
    }

    def test_pipe_format(self):
        result = _render_table(self.SAMPLE_BLOCK, "pipe")
        lines = result.split("\n")
        assert lines[0] == "Name | Age"
        assert lines[1] == "Alice | 30"
        assert lines[2] == "Bob | 25"

    def test_csv_format(self):
        result = _render_table(self.SAMPLE_BLOCK, "csv")
        lines = result.replace("\r\n", "\n").split("\n")
        assert lines[0] == "Name,Age"
        assert lines[1] == "Alice,30"
        assert lines[2] == "Bob,25"

    def test_markdown_format(self):
        result = _render_table(self.SAMPLE_BLOCK, "markdown")
        lines = result.split("\n")
        assert lines[0] == "| Name | Age |"
        assert lines[1] == "| --- | --- |"
        assert lines[2] == "| Alice | 30 |"
        assert lines[3] == "| Bob | 25 |"

    def test_empty_table(self):
        block = {"type": "table", "table_body": []}
        assert _render_table(block, "pipe") == ""

    def test_plain_string_cells(self):
        """table_body may contain plain strings instead of dicts."""
        block = {
            "type": "table",
            "table_body": [["X", "Y"], ["1", "2"]],
        }
        result = _render_table(block, "csv")
        assert "X,Y" in result
        assert "1,2" in result


@pytest.mark.asyncio
async def test_save_content_table_csv(tmp_path):
    """Tables in text mode with --table-format csv should render as CSV."""
    content = [
        {
            "type": "table",
            "table_body": [[{"text": "A"}, {"text": "B"}], [{"text": "1"}, {"text": "2"}]],
        },
    ]
    output_file = tmp_path / "out.txt"

    await save_content(content, output_file, output_format="text", table_format="csv")

    text = output_file.read_text()
    assert "A,B" in text
    assert "1,2" in text


@pytest.mark.asyncio
async def test_save_content_table_markdown(tmp_path):
    """Tables in text mode with --table-format markdown should render as Markdown."""
    content = [
        {
            "type": "table",
            "table_body": [[{"text": "Col1"}, {"text": "Col2"}], [{"text": "a"}, {"text": "b"}]],
        },
    ]
    output_file = tmp_path / "out.txt"

    await save_content(content, output_file, output_format="text", table_format="markdown")

    text = output_file.read_text()
    assert "| Col1 | Col2 |" in text
    assert "| --- | --- |" in text
    assert "| a | b |" in text
