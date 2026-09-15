import base64
import logging
import re
import threading
from concurrent.futures.thread import ThreadPoolExecutor
from itertools import count
from pathlib import Path
from typing import Any

from docling.datamodel.pipeline_options import TableFormerMode
from pypdf import PdfReader

from treant.base_parser import Parser
from treant.constants import HTML_FORMATS, IMAGE_FORMATS, OFFICE_FORMATS, TEXT_FORMATS
from treant.settings import settings

logger = logging.getLogger(__name__)


class DoclingParser(Parser):
    """
    Docling document parsing utility class.
    """

    def __init__(self):
        super().__init__()
        self._converter_cache: dict[tuple, Any] = {}
        self._converter_cache_lock = threading.Lock()
        self._image_executor = ThreadPoolExecutor(max_workers=4)

    def _get_page_idx(self, block: dict[str, Any]) -> int:
        """Extract real page index from Docling provenance data."""
        prov = block.get("prov", [])
        if prov and isinstance(prov, list):
            return prov[0].get("page_no", 0)
        return 0

    # Compiled once at class level for performance
    _LATEX_PATTERNS: re.Pattern = re.compile(
        r"(?:"
        r"\\(?:frac|sqrt|sum|prod|int|lim|infty|partial|nabla|alpha|beta|gamma|delta"
        r"|epsilon|theta|lambda|mu|sigma|omega|phi|psi|pi|cdot|times|div|pm|mp|leq|geq"
        r"|neq|approx|equiv|subset|supset|cup|cap|in|notin|forall|exists|mathbb|mathcal"
        r"|mathrm|text|left|right|over|under|hat|bar|vec|dot|ddot|tilde"
        r"|log|ln|sin|cos|tan|exp|det|max|min|sup|inf|lim)\b"
        r"|\\begin\{[^}]+\}"
        r"|\\end\{[^}]+\}"
        r"|\\[(\[]"  # \( or \[
        r"|\\[)\]]"  # \) or \]
        r"|\$\$.+?\$\$"  # display math $$...$$
        r"|\$.+?\$"  # inline math $...$
        r"|[_^]\{[^}]+\}"  # sub/superscript with braces: x_{i}, e^{2}
        r")",
        re.DOTALL,
    )

    @staticmethod
    def _detect_latex(text: str) -> str:
        """Return ``'latex'`` if *text* contains LaTeX markup, else ``'plain'``."""
        if DoclingParser._LATEX_PATTERNS.search(text):
            return "latex"
        return "plain"

    def _write_image(self, image_path: Path, base64_str: str) -> None:
        """Write image bytes to disk — runs in thread pool."""
        image_path.parent.mkdir(parents=True, exist_ok=True)
        with open(image_path, "wb") as f:
            f.write(base64.b64decode(base64_str))

    def _get_converter(self):
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption

        table_mode = str(settings.TABLE_MODE).lower()
        do_tables = settings.DO_TABLES
        do_ocr = settings.DO_OCR

        cache_key = (table_mode, do_tables, do_ocr)
        with self._converter_cache_lock:
            if cache_key in self._converter_cache:
                return self._converter_cache[cache_key]

            pipeline_options = PdfPipelineOptions()

            if hasattr(pipeline_options, "do_ocr"):
                pipeline_options.do_ocr = do_ocr

            if hasattr(pipeline_options, "do_table_structure"):
                pipeline_options.do_table_structure = do_tables

            if do_tables and hasattr(pipeline_options, "table_structure_options"):
                try:
                    pipeline_options.table_structure_options.mode = (
                        TableFormerMode.ACCURATE
                        if table_mode == "accurate"
                        else TableFormerMode.FAST
                    )
                except Exception as e:
                    logger.debug(f"Could not set TableFormer mode '{table_mode}': {e}")

            # Only generate images if OCR or image extraction is needed
            generate_images = getattr(settings, "GENERATE_IMAGES", True)

            if hasattr(pipeline_options, "generate_picture_images"):
                pipeline_options.generate_picture_images = generate_images

            if hasattr(pipeline_options, "images_scale"):
                pipeline_options.images_scale = (
                    1.0 if not generate_images else getattr(settings, "IMAGES_SCALE", 2.0)
                )

            converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
            )

            self._converter_cache[cache_key] = converter
            return converter

    def _read_from_block_recursive(
        self,
        block,
        type: str,
        output_dir: Path,
        counter,
        num: str,
        docling_content: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Recursively traverse the Docling document block tree.

        Args:
            block: Current block dict from the document.
            block_type: The type key of this block (e.g. 'texts', 'pictures', 'body').
            output_dir: Directory for image output.
            counter: A shared itertools.count() object for stable indexing.
            num: The string index of this block within its type array.
            docling_content: The full exported document dict for $ref resolution.
        """
        content_list = []
        if not block.get("children"):
            next(counter)
            content_list.append(self._read_from_block(block, type, output_dir, num, counter))
        else:
            if type not in ["groups", "body"]:
                next(counter)
                content_list.append(self._read_from_block(block, type, output_dir, num, counter))
            members = block["children"]
            for member in members:
                next(counter)
                member_tag = member["$ref"]
                # JSON References follow the form "#/<type>/<index>" (e.g. "#/body/0")
                ref_parts = member_tag.split("/")
                if len(ref_parts) < 3:
                    logger.warning(
                        f"Unexpected $ref format (expected #/<type>/<index>): {member_tag!r}"
                    )
                    continue
                member_type = ref_parts[1]
                member_num = ref_parts[2]
                try:
                    member_block = docling_content[member_type][int(member_num)]
                except (KeyError, ValueError, IndexError) as e:
                    logger.warning(f"Could not resolve $ref {member_tag!r}: {e}")
                    continue
                content_list.extend(
                    self._read_from_block_recursive(
                        member_block,
                        member_type,
                        output_dir,
                        counter,
                        member_num,
                        docling_content,
                    )
                )
        return content_list

    def _read_from_block(
        self, block, type: str, output_dir: Path, num: str, counter
    ) -> dict[str, Any]:
        page_idx = self._get_page_idx(block)

        if type == "texts":
            if block["label"] == "formula":
                text_format = self._detect_latex(block["orig"])
                return {
                    "type": "equation",
                    "img_path": "",
                    "text": block["orig"],
                    "text_format": text_format,
                    "page_idx": page_idx,
                }
            else:
                return {
                    "type": "text",
                    "text": block["orig"],
                    "page_idx": page_idx,
                }
        elif type == "pictures":
            try:
                base64_uri = block["image"]["uri"]
                parts = base64_uri.split(",", 1)
                base64_str = parts[1] if len(parts) == 2 else parts[0]
                image_dir = output_dir / "images"
                image_dir.mkdir(parents=True, exist_ok=True)
                image_path = image_dir / f"image_{next(counter)}.png"

                self._image_executor.submit(self._write_image, image_path, base64_str)

                return {
                    "type": "image",
                    "img_path": str(image_path.resolve()),
                    "image_caption": block.get("caption", ""),
                    "image_footnote": block.get("footnote", ""),
                    "page_idx": page_idx,
                }
            except Exception as e:
                logger.warning(f"Failed to process image {num}: {e}")
                return {
                    "type": "text",
                    "text": f"[Image processing failed: {block.get('caption', '')}]",
                    "page_idx": page_idx,
                }
        else:
            try:
                return {
                    "type": "table",
                    "img_path": "",
                    "table_caption": block.get("caption", ""),
                    "table_footnote": block.get("footnote", ""),
                    "table_body": block.get("data", []),
                    "page_idx": page_idx,
                }
            except Exception as e:
                logger.warning(f"Failed to process table {num}: {e}")
                return {
                    "type": "text",
                    "text": f"[Table processing failed: {block.get('caption', '')}]",
                    "page_idx": page_idx,
                }

    def _get_pdf_page_ranges(self, file_path: Path) -> list[tuple[int, int] | None]:
        if file_path.suffix.lower() != ".pdf":
            return [None]

        threshold = getattr(settings, "PDF_CHUNK_THRESHOLD_PAGES", 150)
        chunk_size = getattr(settings, "PDF_CHUNK_SIZE_PAGES", 75)

        try:
            num_pages = len(PdfReader(str(file_path)).pages)
        except Exception as e:
            logger.warning(f"Could not read page count for {file_path.name}: {e}")
            return [None]

        if num_pages <= threshold:
            return [None]

        return [
            (start, min(start + chunk_size - 1, num_pages))
            for start in range(1, num_pages + 1, chunk_size)
        ]

    def _parse_with_converter(
        self,
        file_path: Path,
        output_dir: str | None,
    ) -> list[dict[str, Any]]:
        """
        Shared core logic for all supported formats.
        Converts the document (optionally in page-range chunks for large PDFs),
        traverses the block tree, and frees doc_dict after each chunk.
        """
        converter = self._get_converter()

        if output_dir:
            base_output_dir = self._unique_output_dir(output_dir, file_path)
        else:
            base_output_dir = file_path.parent / "docling_output"

        base_output_dir.mkdir(parents=True, exist_ok=True)

        content_list: list[dict[str, Any]] = []
        image_counter = count()
        for page_range in self._get_pdf_page_ranges(file_path):
            convert_kwargs = {"page_range": page_range} if page_range else {}
            result = converter.convert(str(file_path), **convert_kwargs)

            if result.status.name != "SUCCESS":
                logger.warning(
                    f"Docling returned status={result.status.name} for "
                    f"{file_path.name} (page_range={page_range}); errors: {result.errors}"
                )

            doc_dict = result.document.export_to_dict()
            try:
                content_list.extend(
                    self._read_from_block_recursive(
                        doc_dict["body"],
                        "body",
                        base_output_dir,
                        image_counter,
                        "0",
                        doc_dict,
                    )
                )
            finally:
                del doc_dict

        return content_list

    def check_installation(self) -> bool:
        try:
            from docling.document_converter import DocumentConverter  # noqa: F401

            return True
        except ImportError:
            logger.debug(
                "Docling Python package is not installed. Install it with: pip install docling"
            )
            return False

    def parse_doc(
        self,
        file_path: str | Path,
        output_dir: str | None = None,
        method: str = "auto",
        lang: str | None = None,
        **kwargs,
    ):
        try:
            file_path = Path(file_path)
            if not file_path.exists():
                raise FileNotFoundError(f"Document file does not exist: {file_path}")

            ext = file_path.suffix.lower()

            if ext == ".pdf":
                return self.parse_pdf(file_path)
            elif ext in HTML_FORMATS:
                return self.parse_html(file_path)
            elif ext in OFFICE_FORMATS:
                return self.parse_office(file_path)
            elif ext in TEXT_FORMATS:
                return self.parse_text_file(file_path)
            else:
                raise ValueError(
                    f"Unsupported file format: {ext}. "
                    f"Docling only supports PDF files, Office formats ({', '.join(OFFICE_FORMATS)}) "
                    f"and HTML formats ({', '.join(HTML_FORMATS)})"
                )
        except Exception as e:
            logger.error(f"Error in parse file_path: {str(e)}")
            raise

    def parse_pdf(
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
                raise FileNotFoundError(f"PDF file does not exist: {pdf_path}")

            return self._parse_with_converter(pdf_path, output_dir)

        except Exception as e:
            logger.error(f"Error in parse pdf: {str(e)}")
            raise

    def parse_html(self, file_path: str | Path, output_dir: str | None = None, **kwargs):
        html_path = Path(file_path)
        if not html_path.exists():
            raise FileNotFoundError(f"HTML file does not exist: {html_path}")

        if html_path.suffix.lower() not in HTML_FORMATS:
            raise ValueError(f"Unsupported HTML format: {html_path.suffix}")

        try:
            name_without_suff = html_path.stem

            logger.info(f"Parsing {name_without_suff} html")

            with open(html_path, encoding="utf-8", errors="ignore") as f:
                content = f.read()

            content_list = self.extract_html_content(content)

            return content_list

        except Exception as e:
            logger.error(f"Error in parse html: {str(e)}")
            return self._parse_with_converter(html_path, output_dir)

    def parse_office(self, file_path: str | Path, output_dir: str | None = None, **kwargs):
        try:
            file_path = Path(file_path)
            if not file_path.exists():
                raise FileNotFoundError(f"Office file does not exist: {file_path}")

            return self._parse_with_converter(file_path, output_dir)

        except Exception as e:
            logger.error(f"Error in parse office: {str(e)}")
            raise

    def parse_text_file(self, file_path: str | Path, output_dir: str | None = None, **kwargs):
        try:
            file_path = Path(file_path)
            if not file_path.exists():
                raise FileNotFoundError(f"Text file does not exist: {file_path}")

            name_without_suff = file_path.stem

            logger.info(f"Parsing {name_without_suff}")

            return self._parse_with_converter(file_path, output_dir)

        except Exception as e:
            logger.error(f"Error in parse text: {str(e)}")
            raise

    def parse_image(
        self,
        file_path: str | Path,
        output_dir: str | None = None,
        method: str = "auto",
        lang: str | None = None,
        **kwargs,
    ):
        """Parse an image file using Docling's OCR pipeline.

        Docling's ``DocumentConverter`` natively supports image formats
        (PNG, JPEG, TIFF, BMP, GIF, WebP).  OCR is forced on so that
        text embedded in the image is extracted.
        """
        try:
            image_path = Path(file_path)
            if not image_path.exists():
                raise FileNotFoundError(f"Image file does not exist: {image_path}")

            ext = image_path.suffix.lower()
            if ext not in IMAGE_FORMATS:
                raise ValueError(
                    f"Unsupported image format: {ext}. "
                    f"Supported: {', '.join(sorted(IMAGE_FORMATS))}"
                )

            logger.info(f"Parsing image {image_path.name} with OCR pipeline")
            return self._parse_with_converter(image_path, output_dir)

        except Exception as e:
            logger.error(f"Error in parse image: {str(e)}")
            raise
