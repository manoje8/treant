import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # GCP
    PROJECT_ID: str = os.getenv("PROJECT_ID")
    LOCATION: str = os.getenv("LOCATION")
    GCP_DOC_AI_LOCATION: str = os.getenv("GCP_DOC_AI_LOCATION")
    GCP_DOC_AI_PROCESSOR_ID: str = os.getenv("GCP_DOC_AI_PROCESSOR_ID")

    # docling
    TABLE_MODE = os.getenv("TABLE_MODE", "fast")
    DO_TABLES: bool = os.getenv("DO_TABLES", "true").lower() == "true"
    DO_OCR: bool = os.getenv("DO_OCR", "false").lower() == "true"
    GENERATE_IMAGES: bool = os.getenv("GENERATE_IMAGES", "true").lower == "true"
    IMAGES_SCALE: float = os.getenv("IMAGES_SCALE", 2.0)
    PDF_CHUNK_THRESHOLD_PAGES: int = os.getenv("PDF_CHUNK_THRESHOLD_PAGES", 150)
    PDF_CHUNK_SIZE_PAGES: int = os.getenv("PDF_CHUNK_SIZE_PAGES", 75)

    MAX_PAGE_PER_PARSE: int = int(os.getenv("MAX_PAGE_PER_PARSE", 20))
    MAX_UPLOAD_BYTES: int = int(os.getenv("MAX_UPLOAD_BYTES", 100_000_00))

    CACHE_DIR = Path(".cache/doc_parser")


settings = Settings()
