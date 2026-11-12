
from pathlib import Path

from docling.document_converter import DocumentConverter
from docling_core.types.doc import ImageRefMode

BASE_DIR = Path(__file__).resolve().parent
INPUT = BASE_DIR / "input/N845.odt"
OUTPUT = BASE_DIR / "output/docling/markdown"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)

    document = DocumentConverter().convert(INPUT).document

    document.save_as_markdown(
        OUTPUT / f"{INPUT.stem}.md",
        image_mode=ImageRefMode.REFERENCED,
    )


if __name__ == "__main__":
    main()
