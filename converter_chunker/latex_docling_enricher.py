"""Enrich converted images with descriptions and LaTeX before chunking."""

import logging

from pyimport_image_assets import missing_descriptions

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import ConvertPipelineOptions, PictureDescriptionVlmEngineOptions
from docling.datamodel.vlm_engine_options import MlxVlmEngineOptions
from docling.document_converter import DocumentConverter, DoclingJSONFormatOption
from docling_core.types.doc import ImageRefMode

from pyimport_pipeline_config import DOCUMENT_JSON as INPUT, LATEX_OUTPUT_DIR as OUTPUT, LATEX_JSON


def build_options():
    descriptions = PictureDescriptionVlmEngineOptions.from_preset(
        "smolvlm", engine_options=MlxVlmEngineOptions()
    )
    descriptions.picture_area_threshold = 0.0
    descriptions.batch_size = 1
    descriptions.prompt = (
        "Describe the visible content for document retrieval. Transcribe visible text "
        "and mathematical formulas (use $...$ for inline LaTeX and $$...$$ for display "
        "LaTeX; do not use code fences), preserving numbers, subscripts and "
        "superscripts. For diagrams explain labels and relationships. "
        "Do not invent missing information. State when content is unreadable."
    )
    descriptions.generation_config = {"max_new_tokens": 512, "do_sample": False}
    return ConvertPipelineOptions(
        do_picture_description=True,
        picture_description_options=descriptions,
    )


def require_gpu():
    """Fail explicitly rather than silently running recognition on CPU."""
    try:
        import mlx.core as mx
        import mlx_vlm  # noqa: F401
    except ImportError as error:
        raise RuntimeError(
            "GPU recognition requires mlx and mlx-vlm in hse_2026_year_project. "
            "Install them before restarting the LaTeX stage."
        ) from error
    if not mx.metal.is_available():
        raise RuntimeError(
            "Apple Metal GPU is unavailable. Run outside the sandbox with GPU access."
        )
    mx.set_default_device(mx.gpu)
    print(f"Recognition engine: MLX; device: {mx.default_device()}", flush=True)


def main():
    if not INPUT.is_file():
        raise FileNotFoundError(f"File not found: {INPUT}")
    require_gpu()
    logging.basicConfig(level=logging.INFO)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    print("Recognizing image text and LaTeX formulas (the first run downloads the model)...")
    converter = DocumentConverter(
        format_options={InputFormat.JSON_DOCLING: DoclingJSONFormatOption(
            pipeline_options=build_options(),
        )}
    )
    document = converter.convert(INPUT).document
    missing = missing_descriptions(document)
    if missing:
        raise RuntimeError(f"Image descriptions were not generated: {', '.join(missing)}")
    document.save_as_json(
        LATEX_JSON, image_mode=ImageRefMode.EMBEDDED, ensure_ascii=False
    )
    print(f"Images with descriptions: {len(document.pictures)}")
    print(f"Results saved to: {OUTPUT}")


if __name__ == "__main__":
    main()
