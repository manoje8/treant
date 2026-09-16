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


async def test_parse_pdf_not_found(parser, tmp_path):
    with pytest.raises(FileNotFoundError, match="PDF file doesn't exist"):
        await parser.parse_pdf(tmp_path / "nonexistent.pdf")


async def test_parse_doc_not_found(parser, tmp_path):
    with pytest.raises(FileNotFoundError, match="Office document doesn't exist"):
        await parser.parse_doc(tmp_path / "nonexistent.docx")


async def test_parse_doc_unsupported(parser, tmp_path):
    invalid_file = tmp_path / "invalid.xyz"
    invalid_file.write_text("dummy")

    with pytest.raises(ValueError, match="Unsupported Office format"):
        await parser.parse_doc(invalid_file)


async def test_parse_html_not_found(parser, tmp_path):
    with pytest.raises(FileNotFoundError, match="HTML file doesn't exist"):
        await parser.parse_html(tmp_path / "nonexistent.html")


async def test_parse_html_unsupported(parser, tmp_path):
    invalid_file = tmp_path / "invalid.xyz"
    invalid_file.write_text("dummy")

    with pytest.raises(ValueError, match="Unsupported HTML format"):
        await parser.parse_html(invalid_file)


@patch.object(GoogleDocAI, "_process_pdf")
async def test_parse_pdf_success(mock_process_pdf, parser, tmp_path):
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_text("dummy")

    mock_process_pdf.return_value = [{"type": "text", "text": "pdf content"}]

    result = await parser.parse_pdf(pdf_file)

    assert result == [{"type": "text", "text": "pdf content"}]
    mock_process_pdf.assert_called_once_with(pdf_file, lang=None)


@patch.object(GoogleDocAI, "_call_doc_ai")
async def test_parse_doc_success(mock_process, parser, tmp_path):
    doc_file = tmp_path / "test.docx"
    doc_file.write_text("dummy")

    mock_process.return_value = [{"type": "text", "text": "doc content"}]

    result = await parser.parse_doc(doc_file)

    assert result == [{"type": "text", "text": "doc content"}]
    mock_process.assert_called_once()
    args, kwargs = mock_process.call_args
    assert args[0] == b"dummy"
    assert args[1] == ".docx"
    assert args[2] == "test"


@patch.object(GoogleDocAI, "extract_html_content")
async def test_parse_html_success(mock_process, parser, tmp_path):
    html_file = tmp_path / "test.html"
    html_file.write_text("dummy html")

    mock_process.return_value = [{"type": "text", "text": "html content"}]

    result = await parser.parse_html(html_file)

    assert result == [{"type": "text", "text": "html content"}]
    mock_process.assert_called_once()
    args, kwargs = mock_process.call_args
    assert args[0] == "dummy html"


def test_call_doc_ai_sets_language_hints(parser, mock_documentai_client):
    """_call_doc_ai must build ProcessOptions with language_hints when lang is provided."""
    from unittest.mock import MagicMock, patch

    from google.cloud import documentai

    mock_result = MagicMock()
    mock_result.document.text = "bonjour"
    parser.client.process_document.return_value = mock_result
    parser.client.processor_path.return_value = "projects/p/locations/l/processors/pr"

    with patch("treant.settings.settings") as mock_settings:
        mock_settings.PROJECT_ID = "p"
        mock_settings.GCP_DOC_AI_LOCATION = "l"
        mock_settings.GCP_DOC_AI_PROCESSOR_ID = "pr"

        # Reset so patch takes effect
        parser.client.processor_path.return_value = "projects/p/locations/l/processors/pr"

        result = parser._call_doc_ai(b"content", ".pdf", "test", lang="fr")

    assert result == "bonjour"
    call_args = parser.client.process_document.call_args
    request: documentai.ProcessRequest = call_args.kwargs.get("request") or call_args.args[0]
    assert request.process_options is not None
    hints = request.process_options.ocr_config.hints
    assert "fr" in list(hints.language_hints)


def test_call_doc_ai_no_language_hints_when_lang_none(parser, mock_documentai_client):
    """_call_doc_ai must NOT set process_options when lang is None."""
    from unittest.mock import MagicMock

    mock_result = MagicMock()
    mock_result.document.text = "hello"
    parser.client.process_document.return_value = mock_result
    parser.client.processor_path.return_value = "projects/p/locations/l/processors/pr"

    result = parser._call_doc_ai(b"content", ".pdf", "test", lang=None)

    assert result == "hello"
    call_args = parser.client.process_document.call_args
    from google.cloud import documentai

    request: documentai.ProcessRequest = call_args.kwargs.get("request") or call_args.args[0]
    # ProcessOptions should be absent (falsy / default)
    assert not request.process_options


def test_call_doc_ai_comma_separated_lang(parser, mock_documentai_client):
    """A comma-separated lang value is split into multiple hints."""
    from unittest.mock import MagicMock

    from google.cloud import documentai

    mock_result = MagicMock()
    mock_result.document.text = "text"
    parser.client.process_document.return_value = mock_result
    parser.client.processor_path.return_value = "projects/p/locations/l/processors/pr"

    result = parser._call_doc_ai(b"content", ".pdf", "test", lang="fr,de")

    assert result == "text"
    call_args = parser.client.process_document.call_args
    request: documentai.ProcessRequest = call_args.kwargs.get("request") or call_args.args[0]
    hints = list(request.process_options.ocr_config.hints.language_hints)
    assert "fr" in hints
    assert "de" in hints
