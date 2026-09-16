import asyncio
import io
import logging
from pathlib import Path

from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable
from google.cloud import documentai
from pypdf import PdfReader, PdfWriter

from treant.base_parser import Parser
from treant.constants import GOOGLE_MIME_TYPES, HTML_FORMATS, OFFICE_FORMATS
from treant.settings import settings

logger = logging.getLogger(__name__)


class GoogleDocAI(Parser):
    def __init__(self):
        super().__init__()
        self.client = documentai.DocumentProcessorServiceClient()

    def check_installation(self) -> bool:
        try:
            from google.cloud import documentai  # noqa: F401

            return True
        except ImportError:
            logger.error(
                "google-cloud-documentai is not installed. Install it with: pip install google-cloud-documentai"
            )
            return False

    async def parse_pdf(
        self,
        file_path: str | Path,
        output_dir: str | None = None,
        method: str = "auto",
        lang: str | None = None,
        **kwargs,
    ):
        try:
            pdf_path = Path(file_path)

            if not pdf_path.exists():
                msg = f"PDF file doesn't exist: {pdf_path}"
                logger.error(msg)
                raise FileNotFoundError(msg)

            content = await self._process_pdf(pdf_path, lang=lang)
            return content

        except (FileNotFoundError, ValueError):
            raise
        except Exception as e:
            logger.error(f"Error in parsing pdf: {str(e)}")
            raise

    async def parse_doc(
        self,
        file_path: str | Path,
        output_dir: str | None = None,
        method: str = "auto",
        lang: str | None = None,
        **kwargs,
    ):
        try:
            doc_path = Path(file_path)

            if not doc_path.exists():
                raise FileNotFoundError(f"Office document doesn't exist: {doc_path}")

            if doc_path.suffix.lower() not in OFFICE_FORMATS:
                raise ValueError(f"Unsupported Office format: {doc_path.suffix}")

            name_without_suff = doc_path.stem

            logger.info(f"Parsing {name_without_suff} document")

            ext = doc_path.suffix

            with open(doc_path, "rb") as f:
                content = f.read()

            content = await asyncio.to_thread(
                self._call_doc_ai, content, ext, name_without_suff, lang
            )
            return content
        except Exception as e:
            logger.error(f"Error in parsing Document: {str(e)}")
            raise

    async def parse_html(
        self,
        file_path: str | Path,
        output_dir: str | None = None,
        method: str = "auto",
        lang: str | None = None,
        **kwargs,
    ):
        try:
            html_path = Path(file_path)

            if not html_path.exists():
                raise FileNotFoundError(f"HTML file doesn't exist: {html_path}")

            if html_path.suffix.lower() not in HTML_FORMATS:
                raise ValueError(f"Unsupported HTML format: {html_path.suffix}")

            name_without_suff = html_path.stem

            logger.info(f"Parsing {name_without_suff} html")

            with open(html_path, encoding="utf-8", errors="ignore") as f:
                content = f.read()

            content_list = self.extract_html_content(content)

            return content_list

        except Exception as e:
            logger.error(f"Error in parsing HTML: {str(e)}")
            raise

    async def _process_pdf(self, file_path: Path | str, lang: str | None = None):
        file_path = Path(file_path)
        name_without_suffix = file_path.stem
        ext = file_path.suffix

        reader = PdfReader(file_path)
        total_pages = len(reader.pages)
        max_page: int = settings.MAX_PAGE_PER_PARSE

        logger.debug(f"{name_without_suffix}: PDF with {total_pages} pages")

        if total_pages <= max_page:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            return await asyncio.to_thread(
                self._call_doc_ai, file_bytes, ext, name_without_suffix, lang
            )

        logger.info(f"PDF exceeds {max_page} pages, splitting into chunks...")
        parts = []
        for i in range(0, total_pages, max_page):
            writer = PdfWriter()
            split_end = min(i + max_page, total_pages)
            for page_n in range(i, split_end):
                writer.add_page(reader.pages[page_n])

            with io.BytesIO() as bs:
                writer.write(bs)
                file_bytes = bs.getvalue()

            split_text = await asyncio.to_thread(
                self._call_doc_ai, file_bytes, ext, name_without_suffix, lang
            )
            parts.append(split_text)

        return "\n".join(parts)

    def _call_doc_ai(
        self,
        content: bytes,
        ext: str,
        display_name: str | None = None,
        lang: str | None = None,
    ) -> str:
        try:
            ext_key = ext.lower().lstrip(".")
            mime_type = GOOGLE_MIME_TYPES.get(ext_key)

            if not mime_type:
                raise ValueError(f"No MIME type mapping for extension: {ext}")

            processor_name = self.client.processor_path(
                settings.PROJECT_ID,
                settings.GCP_DOC_AI_LOCATION,
                settings.GCP_DOC_AI_PROCESSOR_ID,
            )

            raw_doc = documentai.RawDocument(
                content=content, mime_type=mime_type, display_name=display_name
            )

            # Build process options, adding OCR language hints when lang is given.
            # Doc AI accepts BCP-47 codes (e.g. "fr", "de", "ja") directly.
            process_options: documentai.ProcessOptions | None = None
            if lang:
                language_hints = [code.strip() for code in lang.split(",") if code.strip()]
                if language_hints:
                    logger.debug("Google Doc AI language hints: %s", language_hints)
                    process_options = documentai.ProcessOptions(
                        ocr_config=documentai.OcrConfig(
                            hints=documentai.OcrConfig.Hints(language_hints=language_hints)
                        )
                    )

            request = documentai.ProcessRequest(
                name=processor_name,
                raw_document=raw_doc,
                **({"process_options": process_options} if process_options else {}),
            )

            result = self.client.process_document(request=request)
            return result.document.text

        except ResourceExhausted:
            logger.error("Doc AI quota exhausted")
            raise
        except ServiceUnavailable as e:
            logger.warning(f"Doc AI transiently unavailable: {e}")
            raise
