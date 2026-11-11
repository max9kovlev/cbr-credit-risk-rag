import json
import statistics

from pyimport_document_references import resolve_references, index_references_by_chunk
from pyimport_image_assets import missing_descriptions, image_text_serializer_provider
from pyimport_chunk_export import build_records, write_jsonl, validate_picture_text
from pyimport_pipeline_config import (
    LATEX_JSON as INPUT, OUTPUT_DIR, CHUNKS_JSONL as OUTPUT, SOURCE,
    REFERENCES_JSON, RESOLVED_REFERENCES_JSON, TOKENIZER_MODEL as MODEL, MAX_TOKENS,
)
from pyimport_protected_chunking import protected_chunks

from docling_core.types.doc import DoclingDocument
from docling.chunking import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import (
    HuggingFaceTokenizer,
)
from transformers import AutoTokenizer


def main():
    if not INPUT.exists():
        raise FileNotFoundError(f"File not found: {INPUT}")

    print("Loading document...")

    document = DoclingDocument.load_from_json(INPUT)

    references_path = REFERENCES_JSON
    if not references_path.is_file():
        raise FileNotFoundError(f"Run docling_converter.py first: {references_path}")
    references = json.loads(references_path.read_text(encoding="utf-8"))

    missing = missing_descriptions(document)
    if missing:
        raise ValueError(f"Image descriptions are missing: {', '.join(missing)}")
    image_refs = {picture.self_ref for picture in document.pictures}

    print("Loading tokenizer...")

    hf_tokenizer = AutoTokenizer.from_pretrained(MODEL)

    tokenizer = HuggingFaceTokenizer(
        tokenizer=hf_tokenizer,
        max_tokens=MAX_TOKENS,
    )

    print("Splitting into chunks...")

    chunker = HybridChunker(
        serializer_provider=image_text_serializer_provider(),
        tokenizer=tokenizer,
        merge_peers=True,
        repeat_table_header=True,
    )

    chunks = list(protected_chunks(document, chunker, tokenizer, MAX_TOKENS))

    resolved = resolve_references(chunks, references, document, INPUT.stem)
    links_by_chunk, anchors_by_chunk = index_references_by_chunk(resolved)
    # Build and validate all records before opening final output files.
    records = build_records(
        chunks, contextualize=chunker.contextualize, count_tokens=tokenizer.count_tokens,
        max_tokens=MAX_TOKENS, prefix=INPUT.stem, source=SOURCE.name,
        image_refs=image_refs, links_by_chunk=links_by_chunk,
        anchors_by_chunk=anchors_by_chunk,
    )
    validate_picture_text(document, records)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESOLVED_REFERENCES_JSON.write_text(
        json.dumps(resolved, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_jsonl(records, OUTPUT)
    sizes = [record["tokens"] for record in records]

    print("\n===== STATISTICS =====")

    print(f"Chunk count: {len(chunks)}")

    if sizes:
        print(f"Minimum: {min(sizes)}")
        print(f"Mean: {statistics.mean(sizes):.1f}")
        print(f"Median: {statistics.median(sizes)}")
        print(f"Maximum: {max(sizes)}")
        print(
            f"Exceeding {MAX_TOKENS}: "
            f"{sum(size > MAX_TOKENS for size in sizes)}"
        )

    print(f"\nFile saved: {OUTPUT.resolve()}")


if __name__ == "__main__":
    main()
