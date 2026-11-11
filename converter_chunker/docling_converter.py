import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pyimport_document_references import prepare_odt, remove_markers

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import ConvertPipelineOptions
from docling.document_converter import DocumentConverter, OdtFormatOption
from docling_core.types.doc import ImageRefMode

from pyimport_pipeline_config import (
    SOURCE as INPUT, OUTPUT_DIR as OUTPUT, DOCUMENT_JSON, REFERENCES_JSON,
)


def main():
    if not INPUT.is_file():
        raise FileNotFoundError(f"File not found: {INPUT}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    print("Converting ODT to DoclingDocument JSON...")
    converter = DocumentConverter(
        format_options={InputFormat.ODT: OdtFormatOption(pipeline_options=ConvertPipelineOptions(do_picture_description=False))}
    )
    with TemporaryDirectory(prefix="docling-anchors-") as temporary:
        marked_input = Path(temporary) / INPUT.name
        references = prepare_odt(INPUT, marked_input)
        document = converter.convert(marked_input).document
        remove_markers(document, references)
    REFERENCES_JSON.write_text(
        json.dumps(references, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    document.save_as_json(
        DOCUMENT_JSON, image_mode=ImageRefMode.EMBEDDED, ensure_ascii=False
    )
    print(f"Extracted images: {len(document.pictures)}")
    print(f"Results saved to: {OUTPUT}")


if __name__ == "__main__":
    main()
