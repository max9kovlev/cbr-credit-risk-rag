import json
import statistics
from pathlib import Path

from docling_core.types.doc import DoclingDocument
from docling.chunking import HierarchicalChunker
from docling_core.transforms.chunker.tokenizer.huggingface import (
    HuggingFaceTokenizer,
)
from transformers import AutoTokenizer


BASE_DIR = Path(__file__).resolve().parent
INPUT = BASE_DIR / "output/docling/N845-docling.json"
OUTPUT_DIR = BASE_DIR / "output/docling"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT = OUTPUT_DIR / f"{INPUT.stem}-hierarchical-chunks.jsonl"

MODEL = "BAAI/bge-m3"       # intfloat/multilingual-e5-base      
MAX_TOKENS = 512  

if not INPUT.exists():
    raise FileNotFoundError(f"File not found: {INPUT}")

print("Loading document...")

document = DoclingDocument.load_from_json(INPUT)

image_paths = {}
for index, picture in enumerate(document.pictures):
    if not (picture.meta and picture.meta.description and picture.meta.description.text.strip()):
        raise ValueError("Image description is missing. Run docling_md_converter.py first")
    image = picture.get_image(document)
    if image is None:
        raise ValueError(f"Image data is missing: {picture.self_ref}")
    relative_path = Path(f"{INPUT.stem}_media") / f"docling-picture-{index:05d}.png"
    path = OUTPUT_DIR / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    image_paths[picture.self_ref] = relative_path.as_posix()

print("Loading tokenizer...")

hf_tokenizer = AutoTokenizer.from_pretrained(MODEL)

tokenizer = HuggingFaceTokenizer(
    tokenizer=hf_tokenizer,
    max_tokens=MAX_TOKENS,
)

print("Splitting into hierarchical chunks...")

chunker = HierarchicalChunker(
    merge_list_items=True,
)

chunks = list(chunker.chunk(dl_doc=document))

OUTPUT.parent.mkdir(parents=True, exist_ok=True)

sizes = []

with OUTPUT.open("w", encoding="utf-8") as file:
    for index, chunk in enumerate(chunks):

        text = chunker.contextualize(chunk)
        tokens = tokenizer.count_tokens(text)
        sizes.append(tokens)

        record = {
            "id": f"{INPUT.stem}-hierarchical-{index:05d}",
            "source": f"{INPUT.stem.removesuffix('-docling')}.odt",
            "index": index,
            "headings": chunk.meta.headings or [],
            "text": chunk.text,
            "embedding_text": text,
            "tokens": tokens,
            "images": [
                {"ref": item.self_ref, "path": image_paths[item.self_ref]}
                for item in chunk.meta.doc_items
                if item.self_ref in image_paths
            ],
        }

        file.write(
            json.dumps(record, ensure_ascii=False) + "\n"
        )

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
