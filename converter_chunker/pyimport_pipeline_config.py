"""Shared paths and model settings for all pipeline stages."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SOURCE = BASE_DIR / "input/N845.odt"
OUTPUT_DIR = BASE_DIR / "output/docling"
LATEX_OUTPUT_DIR = BASE_DIR / "output/latex"
DOCUMENT_JSON = OUTPUT_DIR / f"{SOURCE.stem}-docling.json"
LATEX_JSON = LATEX_OUTPUT_DIR / f"{DOCUMENT_JSON.stem}-latex.json"
REFERENCES_JSON = OUTPUT_DIR / f"{SOURCE.stem}-references.json"
RESOLVED_REFERENCES_JSON = OUTPUT_DIR / f"{SOURCE.stem}-resolved-references.json"
CHUNKS_JSONL = OUTPUT_DIR / f"{LATEX_JSON.stem}-hybrid-chunks.jsonl"
TOKENIZER_MODEL = "BAAI/bge-m3"
MAX_TOKENS = 512
