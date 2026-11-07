from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import ConvertPipelineOptions, PictureDescriptionVlmEngineOptions
from docling.document_converter import DocumentConverter, OdtFormatOption
from docling_core.types.doc import ImageRefMode

BASE_DIR = Path(__file__).resolve().parent
INPUT = BASE_DIR / "input/N845.odt"
OUTPUT = BASE_DIR / "output/docling"


def build_options():
    descriptions = PictureDescriptionVlmEngineOptions.from_preset("smolvlm")
    descriptions.picture_area_threshold = 0.0
    descriptions.batch_size = 1
    descriptions.prompt = (
        "Describe the visible content for document retrieval. Transcribe visible text "
        "and mathematical formulas (use LaTeX), preserving numbers, subscripts and "
        "superscripts. For diagrams explain labels and relationships. "
        "Do not invent missing information. State when content is unreadable."
    )
    descriptions.generation_config = {"max_new_tokens": 512, "do_sample": False}
    return ConvertPipelineOptions(
        do_picture_description=True,
        picture_description_options=descriptions,
    )


def main():
    if not INPUT.is_file():
        raise FileNotFoundError(f"File not found: {INPUT}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    print("Converting ODT and describing images (the first run downloads the model)...")
    converter = DocumentConverter(
        format_options={InputFormat.ODT: OdtFormatOption(pipeline_options=build_options())}
    )
    document = converter.convert(INPUT).document
    missing = [
        picture.self_ref for picture in document.pictures
        if not (picture.meta and picture.meta.description and picture.meta.description.text.strip())
    ]
    if missing:
        raise RuntimeError(f"Image descriptions were not generated: {', '.join(missing)}")
    document.save_as_json(
        OUTPUT / f"{INPUT.stem}-docling.json", image_mode=ImageRefMode.EMBEDDED, ensure_ascii=False
    )
    document.save_as_markdown(
        OUTPUT / f"{INPUT.stem}-docling.md", image_mode=ImageRefMode.REFERENCED
    )
    print(f"Images with descriptions: {len(document.pictures)}")
    print(f"Results saved to: {OUTPUT}")


if __name__ == "__main__":
    main()
