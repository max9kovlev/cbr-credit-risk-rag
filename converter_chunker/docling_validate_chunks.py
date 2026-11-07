import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ImageEntry(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    ref: str = Field(min_length=1)
    path: str = Field(min_length=1)


class ChunkRecord(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    index: int = Field(ge=0)
    headings: list[str]
    text: str = Field(min_length=1)
    embedding_text: str = Field(min_length=1)
    tokens: int = Field(gt=0)
    images: list[ImageEntry]


def get_tokenizer(model: str, max_tokens: int):
    
    from docling_core.transforms.chunker.tokenizer.huggingface import (
        HuggingFaceTokenizer,
    )
    from transformers import AutoTokenizer
    return HuggingFaceTokenizer(
        tokenizer=AutoTokenizer.from_pretrained(model), max_tokens=max_tokens
    )


def validate(name, path, image_root, limit, small, tokenizer=None):
    errors, warnings, anomalies, rows = [], [], [], []
    ids, indices = [], []
    total_lines = 0
    with path.open(encoding="utf-8") as stream:
        for line_no, raw in enumerate(stream, 1):
            total_lines += 1
            if not raw.strip():
                errors.append(f"line {line_no}: empty JSONL line")
                continue
            try:
                obj = json.loads(raw)
                chunk = ChunkRecord.model_validate(obj)
            except (ValueError, ValidationError) as exc:
                errors.append(f"line {line_no}: invalid chunk: {str(exc)[:400]}")
                continue

            ids.append(chunk.id)
            indices.append(chunk.index)
            if not chunk.text.strip() or not chunk.embedding_text.strip():
                errors.append(f"line {line_no}: {chunk.id}: blank text")
            if not all(h.strip() for h in chunk.headings):
                warnings.append(f"{chunk.id}: empty heading entry")
            if chunk.text not in chunk.embedding_text:
                
                warnings.append(f"{chunk.id}: original text not literal substring of embedding_text")
            for img in chunk.images:
                if not (image_root / img.path).is_file():
                    errors.append(f"{chunk.id}: missing image file: {image_root / img.path}")

            token_mismatch = False
            if tokenizer is not None:
                actual = tokenizer.count_tokens(chunk.embedding_text)
                token_mismatch = actual != chunk.tokens
                if token_mismatch:
                    errors.append(f"{chunk.id}: tokens stored={chunk.tokens}, recounted={actual}")

            if chunk.tokens > limit or chunk.tokens < small:
                anomalies.append({
                    "method": name, "id": chunk.id, "index": chunk.index,
                    "tokens": chunk.tokens,
                    "issue": "oversized" if chunk.tokens > limit else "small",
                    "preview": chunk.text[:160].replace("\n", " "),
                })
            rows.append({
                "tokens": chunk.tokens,
                "has_headings": bool(chunk.headings),
                "image_count": len(chunk.images),
                "oversized": chunk.tokens > limit,
                "small": chunk.tokens < small,
            })

    duplicates = [k for k, v in Counter(ids).items() if v > 1]
    if duplicates:
        errors.append(f"duplicate chunk IDs: {duplicates[:20]}")
    duplicate_indices = [k for k, v in Counter(indices).items() if v > 1]
    if duplicate_indices:
        errors.append(f"duplicate indices: {duplicate_indices[:20]}")
    if indices != list(range(len(indices))):
        warnings.append("indices do not form consecutive 0-based sequence in file order")
    if not rows:
        errors.append("no valid chunks")

    frame = pd.DataFrame(rows)
    tokens = frame["tokens"] if not frame.empty else pd.Series(dtype="int64")
    result = {
        "method": name,
        "file": str(path),
        "total_lines": total_lines,
        "valid_records": len(rows),
        "technical_valid": not errors,
        "min_tokens": int(tokens.min()) if len(tokens) else None,
        "mean_tokens": round(float(tokens.mean()), 2) if len(tokens) else None,
        "median_tokens": float(tokens.median()) if len(tokens) else None,
        "p95_tokens": float(tokens.quantile(0.95)) if len(tokens) else None,
        "max_tokens": int(tokens.max()) if len(tokens) else None,
        "oversized_chunks": int(frame["oversized"].sum()) if len(rows) else 0,
        "small_chunks": int(frame["small"].sum()) if len(rows) else 0,
        "with_headings": int(frame["has_headings"].sum()) if len(rows) else 0,
        "image_references": int(frame["image_count"].sum()) if len(rows) else 0,
        "duplicate_ids": len(duplicates),
        "errors": errors,
        "warnings": warnings,
    }
    
    return result, anomalies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path(__file__).resolve().parent
    parser.add_argument("--hybrid", type=Path, default=base / "output/docling/N845-docling-hybrid-chunks.jsonl")
    parser.add_argument("--hierarchical", type=Path, default=base / "output/docling/N845-docling-hierarchical-chunks.jsonl")
    parser.add_argument("--out-dir", type=Path, default=base / "output/docling/validation")
    parser.add_argument("--image-root", type=Path, default=None, help="base directory for images[].path (default: parent of each JSONL)")
    parser.add_argument("--limit", type=int, default=512)
    parser.add_argument("--small", type=int, default=50)
    parser.add_argument("--recount-tokens", action="store_true", help="download/load tokenizer and verify stored counts")
    parser.add_argument("--model", default="BAAI/bge-m3")
    args = parser.parse_args()

    for path in (args.hybrid, args.hierarchical):
        if not path.is_file():
            parser.error(f"Missing file: {path}. Pass explicit --hybrid and --hierarchical paths.")
    if args.small < 0 or args.limit < 1:
        parser.error("--limit must be positive and --small nonnegative")

    tokenizer = get_tokenizer(args.model, args.limit) if args.recount_tokens else None
    reports, anomalies = [], []
    for name, path in (("hybrid", args.hybrid), ("hierarchical", args.hierarchical)):
        report, found = validate(name, path, args.image_root or path.parent, args.limit, args.small, tokenizer)
        reports.append(report)
        anomalies.extend(found)
        print(f"{name}: {report['valid_records']} valid records; technical_valid={report['technical_valid']}; "
              f"over {args.limit} tokens={report['oversized_chunks']}; errors={len(report['errors'])}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    detail = {
        "settings": {"limit": args.limit, "small_threshold": args.small,
                     "model": args.model, "tokens_recounted": args.recount_tokens},
        "coverage_check": {
            "status": "NOT_PERFORMED",
            "reason": "Chunk JSONL alone cannot prove full coverage of DoclingDocument. "
                      "Compare N845-docling.json against chunk source references and text in a separate audit."
        },
        "methods": reports,
    }
    (args.out_dir / "docling-chunking-validation.json").write_text(
        json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary_fields = [
        "method", "total_lines", "valid_records", "technical_valid", "min_tokens",
        "mean_tokens", "median_tokens", "p95_tokens", "max_tokens",
        "oversized_chunks", "small_chunks", "with_headings", "image_references", "duplicate_ids"
    ]
    pd.DataFrame([{k: report[k] for k in summary_fields} for report in reports]).to_csv(
        args.out_dir / "docling-chunking-comparison.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(anomalies, columns=["method", "id", "index", "tokens", "issue", "preview"]).to_csv(
        args.out_dir / "docling-chunking-anomalies.csv", index=False, encoding="utf-8-sig"
    )
    print(f"Reports: {args.out_dir.resolve()}")


if __name__ == "__main__":
    main()
