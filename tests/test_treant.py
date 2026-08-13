from unittest.mock import MagicMock, patch

import pytest

from treant.constants import ParseMethod
from treant.treant import (
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
    return parser


@pytest.fixture(autouse=True)
def mock_cache():
    """Patch DocumentCache so no real filesystem cache is used in unit tests."""
    with (
        patch("treant.treant.DocumentCache.get", return_value=None),
        patch("treant.treant.DocumentCache.store"),
    ):
        yield


def test_get_parser_method_docling():
    with patch("treant.treant.DoclingParser") as MockDocling:
        parser = get_parser_method("docling")
        assert parser == MockDocling.return_value


def test_get_parser_method_google():
    with patch("treant.treant.GoogleDocAI") as MockGoogle:
        parser = get_parser_method("google_doc_ai")
        assert parser == MockGoogle.return_value


def test_get_parser_method_invalid():
    with pytest.raises(ValueError, match="Unsupported parser type"):
        get_parser_method("invalid")


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
