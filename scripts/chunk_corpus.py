"""Чанки BGE-M3 ≤512 токенов с контекстом и точными координатами исходного текста."""

from bisect import bisect_right
from urllib.request import urlopen

from prepare_corpus import ROOT, sha
from tokenizers import Tokenizer

REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
CHECKSUM = "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08"
LIMIT = 512
STRUCTURAL = {"heading", "chapter", "section", "appendix", "service", "editorial"}


def load_tokenizer():
    path = ROOT / ".cache/bge-m3-tokenizer.json"
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        with urlopen(
            f"https://huggingface.co/BAAI/bge-m3/resolve/{REVISION}/tokenizer.json",
            timeout=60,
        ) as r:
            data = r.read()
        if sha(data) != CHECKSUM:
            raise ValueError("Хеш токенизатора не совпал")
        path.write_bytes(data)
    if sha(path.read_bytes()) != CHECKSUM:
        raise ValueError("Изменился токенизатор")
    tokenizer = Tokenizer.from_file(str(path))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


def make_chunks(units, documents):
    tokenizer = load_tokenizer()
    docs = {d["document_id"]: d for d in documents if d["selected"]}
    chunks = []
    for unit in units:
        if unit["type"] in STRUCTURAL or unit["heading_only"]:
            continue
        doc = docs[unit["document_id"]]
        title = doc["title"].split("\n")[0]
        address = [
            f"{label} {unit[key]}"
            for key, label in [
                ("appendix", "приложение"),
                ("chapter", "глава"),
                ("article", "статья"),
                ("number", "пункт"),
            ]
            if unit[key]
        ]
        context = " | ".join(
            [doc["number"] or doc["file_id"], *address, title, *unit["headings"]]
        )
        # Полные названия остаются в documents/units; контекст не занимает весь бюджет.
        context = (
            tokenizer.decode(
                tokenizer.encode(context, add_special_tokens=False).ids[:96]
            )
            + "\n\n"
        )
        text, start, part = unit["text"], 0, 0
        ends = [
            end for _, end in tokenizer.encode(text, add_special_tokens=False).offsets
        ]
        budget = LIMIT - len(tokenizer.encode(context).ids) - 4
        while start < len(text):
            index = bisect_right(ends, start)
            stop_index = min(index + budget, len(ends)) - 1
            end = ends[stop_index] if stop_index >= 0 else len(text)
            if stop_index == len(ends) - 1:
                end = len(text)
            if end < len(text):
                for marker in ("\n\n", "\n", ". ", "; ", " "):
                    boundary = text.rfind(marker, start, end)
                    if boundary > start + (end - start) // 2:
                        end = boundary + len(marker)
                        break
            embedding = context + text[start:end]
            while len(tokenizer.encode(embedding).ids) > LIMIT and end > start:
                end -= 1
                embedding = context + text[start:end]
            if end <= start:
                raise ValueError(f"Невозможно разбить единицу {unit['id']}")
            if text[start:end].strip():
                part += 1
                chunks.append(
                    {
                        "id": f"{unit['id']}#chunk-{part}",
                        "document_id": unit["document_id"],
                        "unit_id": unit["id"],
                        "parent_id": unit["parent_id"],
                        "start": start,
                        "end": end,
                        "text": text[start:end],
                        "embedding_text": embedding,
                        "tokens": len(tokenizer.encode(embedding).ids),
                    }
                )
            start = end
    return chunks
