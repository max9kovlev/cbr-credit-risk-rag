"""Build, validate and write RAG chunk records."""

import json


def build_records(chunks, *, contextualize, count_tokens, max_tokens, prefix,
                  source, image_refs, links_by_chunk, anchors_by_chunk):
    chunk_refs = [{item.self_ref for item in chunk.meta.doc_items} for chunk in chunks]
    records = []
    for index, chunk in enumerate(chunks):
        text = contextualize(chunk)
        tokens = count_tokens(text)
        if tokens > max_tokens:
            raise ValueError(f"Chunk {index} exceeds the strict limit: {tokens} > {max_tokens}")
        record = {
            "id": f"{prefix}-{index:05d}",
            "source": source,
            "index": index,
            "anchors": anchors_by_chunk.get(f"{prefix}-{index:05d}", []),
            "links": links_by_chunk.get(f"{prefix}-{index:05d}", []),
            "headings": chunk.meta.headings or [],
            "text": chunk.text,
            "embedding_text": text,
            "tokens": tokens,
            "source_item_refs": sorted(chunk_refs[index]),
            "previous_chunk_id": f"{prefix}-{index - 1:05d}" if index else None,
            "next_chunk_id": f"{prefix}-{index + 1:05d}" if index + 1 < len(chunks) else None,
            "continues_previous": bool(index and chunk_refs[index] & chunk_refs[index - 1]),
            "continues_next": bool(index + 1 < len(chunks) and chunk_refs[index] & chunk_refs[index + 1]),
            "images": [
                {"ref": item.self_ref}
                for item in chunk.meta.doc_items
                if item.self_ref in image_refs
            ],
        }
        records.append(record)
    return records


def write_jsonl(records, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def validate_picture_text(document, records):
    """Fail before export if picture text/formulas are lost or assigned elsewhere."""
    from pyimport_protected_chunking import MATH

    def compact(text):
        return "".join(text.split())

    for picture in document.pictures:
        description = picture.meta.description if picture.meta else None
        if not description or not description.text.strip():
            raise ValueError(f"Image description is missing: {picture.self_ref}")
        linked = [r for r in records if picture.self_ref in r["source_item_refs"]]
        if not linked:
            raise ValueError(f"Image has no text chunks: {picture.self_ref}")
        # Parts may be split across chunks; ignore only whitespace differences.
        combined = compact("".join(r["text"] for r in linked))
        if compact(description.text) not in combined:
            raise ValueError(f"Image description was lost or reordered: {picture.self_ref}")
        for field in ("text", "embedding_text"):
            for match in MATH.finditer(description.text):
                formula = match.group()
                if not any(formula in r[field] for r in linked):
                    raise ValueError(
                        f"LaTeX missing or split in {field} for {picture.self_ref}: {formula}"
                    )
        # Contextualization must retain the body, not merely its headings.
        if any(r["text"] not in r["embedding_text"] for r in linked):
            raise ValueError(f"Image chunk text missing from embedding_text: {picture.self_ref}")
