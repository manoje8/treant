from unittest.mock import patch

import pytest

from treant.google_document_ai import GoogleDocAI


@pytest.fixture
def mock_documentai_client():
    with patch(
        "treant.google_document_ai.documentai.DocumentProcessorServiceClient"
    ) as mock_client:
        yield mock_client


@pytest.fixture
def parser(mock_documentai_client):
    return GoogleDocAI()


def test_check_installation(parser):
    assert parser.check_installation() is True


def test_parse_pdf_not_found(parser, tmp_path):
    with pytest.raises(FileNotFoundError, match="PDF file doesn't exist"):
        parser.parse_pdf(tmp_path / "nonexistent.pdf")


def test_parse_doc_not_found(parser, tmp_path):
    with pytest.raises(FileNotFoundError, match="Office document doesn't exist"):
        parser.parse_doc(tmp_path / "nonexistent.docx")


def test_parse_doc_unsupported(parser, tmp_path):
    invalid_file = tmp_path / "invalid.xyz"
    invalid_file.write_text("dummy")

    with pytest.raises(ValueError, match="Unsupported Office format"):
        parser.parse_doc(invalid_file)


def test_parse_html_not_found(parser, tmp_path):
    with pytest.raises(FileNotFoundError, match="HTML file doesn't exist"):
        parser.parse_html(tmp_path / "nonexistent.html")


def test_parse_html_unsupported(parser, tmp_path):
    invalid_file = tmp_path / "invalid.xyz"
    invalid_file.write_text("dummy")

    with pytest.raises(ValueError, match="Unsupported HTML format"):
        parser.parse_html(invalid_file)


@patch.object(GoogleDocAI, "_process_pdf")
def test_parse_pdf_success(mock_process_pdf, parser, tmp_path):
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_text("dummy")

    mock_process_pdf.return_value = [{"type": "text", "text": "pdf content"}]

    result = parser.parse_pdf(pdf_file)

    assert result == [{"type": "text", "text": "pdf content"}]
    mock_process_pdf.assert_called_once_with(pdf_file)


@patch.object(GoogleDocAI, "_process_with_doc_ai")
def test_parse_doc_success(mock_process, parser, tmp_path):
    doc_file = tmp_path / "test.docx"
    doc_file.write_text("dummy")

    mock_process.return_value = [{"type": "text", "text": "doc content"}]

    result = parser.parse_doc(doc_file)

    assert result == [{"type": "text", "text": "doc content"}]
    mock_process.assert_called_once()
    args, kwargs = mock_process.call_args
    assert args[0] == b"dummy"
    assert args[1] == ".docx"
    assert args[2] == "test"


@patch.object(GoogleDocAI, "extract_html_content")
def test_parse_html_success(mock_process, parser, tmp_path):
    html_file = tmp_path / "test.html"
    html_file.write_text("dummy html")

    mock_process.return_value = [{"type": "text", "text": "html content"}]

    result = parser.parse_html(html_file)

    assert result == [{"type": "text", "text": "html content"}]
    mock_process.assert_called_once()
    args, kwargs = mock_process.call_args
    assert args[0] == "dummy html"
