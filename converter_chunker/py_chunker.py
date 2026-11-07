
import json
import re
import statistics
from pathlib import Path
from transformers import AutoTokenizer


BASE_DIR = Path(__file__).resolve().parent
INPUT = BASE_DIR / "output/pandoc/N845-pandoc.md"
OUTPUT = BASE_DIR / "output/pandoc"

MODEL = "BAAI/bge-m3"
MAX_TOKENS = 512

OUTPUT.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT / f"{INPUT.stem}-chunks.jsonl"

if not INPUT.is_file():
    raise FileNotFoundError(f"File not found: {INPUT}")

print("Loading tokenizer...")
tokenizer = AutoTokenizer.from_pretrained(MODEL)


def count_tokens(text):
    return len(tokenizer.encode(text, add_special_tokens=True))


def split_blocks(markdown):
    return [
        block.strip()
        for block in re.split(r"\n\s*\n|\n(?=#{1,6}\s)", markdown)
        if block.strip()
    ]


def split_long_block(text, heading):
    prefix = f"{heading}\n\n" if heading else ""
    while text:
        if count_tokens(prefix + text) <= MAX_TOKENS:
            yield text
            return

        low, high, end = 1, len(text), 0
        while low <= high:
            middle = (low + high) // 2
            if count_tokens(prefix + text[:middle]) <= MAX_TOKENS:
                end = middle
                low = middle + 1
            else:
                high = middle - 1
        if not end:
            raise ValueError(
                "The heading leaves no room for text within the limit of "
                f"{MAX_TOKENS} tokens: {heading!r}"
            )

        boundaries = list(re.finditer(r"\s+", text[:end]))
        if boundaries:
            boundary = boundaries[-1].end()
            if boundary >= end // 2 and count_tokens(prefix + text[:boundary]) <= MAX_TOKENS:
                end = boundary
        yield text[:end]
        text = text[end:]


def make_chunks(markdown):
    chunks = []
    current = []
    heading = ""

    def flush():
        nonlocal current
        if current:
            chunks.append({
                "heading": heading,
                "text": "\n\n".join(current),
            })
            current = []

    for block in split_blocks(markdown):
        if re.match(r"^#{1,6}\s", block):
            flush()
            heading, _, block = block.partition("\n")
            if not block.strip():
                continue

        if re.match(r"^(?:\[\]\{[^}]*\})*\d+(?:\.\d+)+\.?\s", block):
            flush()

        candidate = "\n\n".join(current + [block])
        contextual = f"{heading}\n\n{candidate}".strip()

        if current and count_tokens(contextual) > MAX_TOKENS:
            flush()

        parts = list(split_long_block(block, heading))
        for part in parts[:-1]:
            current.append(part)
            flush()
        current.append(parts[-1])

    flush()
    return chunks


print("Loading Markdown...")
markdown = INPUT.read_text(encoding="utf-8")

print("Splitting into chunks...")
chunks = make_chunks(markdown)

sizes = []

with OUTPUT_FILE.open("w", encoding="utf-8") as file:
    for index, chunk in enumerate(chunks):
        heading = chunk["heading"]
        text = chunk["text"]

        embedding_text = (
            f"{heading}\n\n{text}" if heading else text
        )

        tokens = count_tokens(embedding_text)
        sizes.append(tokens)

        record = {
            "id": f"{INPUT.stem}-{index:05d}",
            "source": INPUT.name,
            "index": index,
            "heading": heading,
            "text": text,
            "embedding_text": embedding_text,
            "tokens": tokens,
            "over_limit": tokens > MAX_TOKENS,
            "converter": "pandoc",
            "tokenizer": MODEL,
        }

        file.write(json.dumps(record, ensure_ascii=False) + "\n")


print("\n===== STATISTICS =====")
print(f"Chunk count: {len(chunks)}")

if sizes:
    print(f"Minimum: {min(sizes)}")
    print(f"Mean: {statistics.mean(sizes):.1f}")
    print(f"Median: {statistics.median(sizes)}")
    print(f"Maximum: {max(sizes)}")
    print(f"Exceeding {MAX_TOKENS}: {sum(s > MAX_TOKENS for s in sizes)}")

print(f"\nFile saved: {OUTPUT_FILE.resolve()}")
