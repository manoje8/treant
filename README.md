# Treant

A simple document parser using Docling and Google Document AI.

Treant allows you to extract structured content from various document formats like PDFs, Office documents (Word, PowerPoint, Excel), HTML, and text files. It provides a simple command-line interface and extensible backend parsers.

## Features

- Parse PDF files with OCR and layout analysis
- Parse Office documents (.docx, .pptx, .xlsx, etc.)
- Parse HTML and Text files
- Support for multiple parsing backends (`docling`, `google_doc_ai`)
- Save extracted content to a file or display extraction statistics

## Installation

This project uses `uv` for dependency management.

1. Ensure you have `uv` installed.
2. Install the project dependencies:
   ```bash
   uv sync
   ```
3. Set up environment variables (if using Google Document AI):
   ```bash
   cp .env.example .env # Or create a .env file with appropriate GCP variables
   ```

## Usage

You can use the `main.py` CLI to parse documents:

```bash
uv run main.py path/to/document.pdf
```

### Options

- `--parse-method`: Choose the parsing backend. Options are `docling` (default) or `google_doc_ai`.
- `-o`, `--output`: Save extracted content to a specified text file.
- `--stats`: Display content extraction statistics.
- `-v`, `--verbose`: Enable verbose logging output.

### Examples

Parse a PDF using default docling parser:
```bash
uv run main.py document.pdf
```

Parse a Word document using Google Document AI:
```bash
uv run main.py document.docx --parse-method google_doc_ai
```

Parse HTML, save output to a file, and show stats:
```bash
uv run main.py document.html -o output.txt --stats
```

## Using treant as a library in other projects

Treant is a regular installable Python package (`pyproject.toml` + `hatchling`
build backend), so any other project can install it and import it directly —
you don't need to copy code between repos.

**During active development** (edit here, see changes everywhere immediately):
```bash
# with uv, from inside the other project
uv add --editable /path/to/treant

# or with plain pip
pip install -e /path/to/treant
```

**Once it's stable, install straight from this GitHub repo:**
```bash
uv add git+https://github.com/manoje8/treant.git
# or
pip install git+https://github.com/manoje8/treant.git
```

Then, in the other project:
```python
from treant import process_document, ParseMethod

content = await process_document("file.pdf", ParseMethod.DOCLING)
```

The CLI (`main.py` / the `treant` console script) is just a thin wrapper
around this same `process_document` function — the reusable logic lives in
`treant/api.py`, not in `main.py`, so it's importable without pulling in
`argparse` or any CLI-only code.

## Testing

Comprehensive test cases have been added for the CLI logic and parser backends using `pytest`.

To run the test suite, use:

```bash
PYTHONPATH=. uv run pytest tests/
```

This will run tests for:
- CLI argument parsing and file processing (`test_main.py`)
- Docling parser component logic (`test_docling.py`)
- Google Document AI parser component logic (`test_google_document_ai.py`)