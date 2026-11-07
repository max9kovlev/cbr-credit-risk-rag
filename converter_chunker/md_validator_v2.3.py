import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

from markdown_it import MarkdownIt
from odfdo import Document
from rapidfuzz import fuzz, process

BASE_DIR = Path(__file__).resolve().parent
INPUT = BASE_DIR / "input/N845.odt"
DOCLING_MD = BASE_DIR / "output/docling/N845-docling.md"
PANDOC_MD = BASE_DIR / "output/pandoc/N845-pandoc.md"
OUTPUT = BASE_DIR / "output/md_validation"
SIMILARITY_THRESHOLD = 90

parser = MarkdownIt("commonmark").enable("table")


def normalize(text):
    return " ".join(text.replace("\u00a0", " ").casefold().split())


def inline_plain(children, images=None):
    parts = []
    for child in children or []:
        if child.type in ("text", "code_inline", "html_inline", "math_inline", "math_block"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif child.type == "image":
            if images is not None:
                images.append(child.attrGet("src"))
            
            if child.children:
                parts.append(inline_plain(child.children))
            elif child.content:
                parts.append(child.content)
        elif child.children:
            parts.append(inline_plain(child.children, images))
    return "".join(parts)


def plain_markdown(text):
    tokens = parser.parseInline(text)
    return inline_plain(tokens[0].children if tokens else [])


def read_odt(path):
    doc = Document(str(path))
    body = doc.body
    
    
    paragraphs = [p.inner_text.strip() for p in body.get_paragraphs() if p.inner_text.strip()]
    headings = [h.inner_text.strip() for h in body.get_headers() if h.inner_text.strip()]
    return {
        "paragraphs": paragraphs + headings,
        "table_count": len(body.get_tables()),
        "image_count": len(body.get_images()),
    }


def read_markdown(path):
    source = path.read_text(encoding="utf-8")
    tokens = parser.parse(source)
    paragraphs, images = [], []
    table_count = 0
    html_table_tags = 0
    for token in tokens:
        if token.type == "table_open":
            table_count += 1
        if token.type in ("html_block", "html_inline"):
            html_table_tags += len(re.findall(r"<table\b", token.content, re.IGNORECASE))
        if token.type == "inline":
            text = inline_plain(token.children, images).strip()
            if text:
                paragraphs.append(text)
            for child in token.children or []:
                if child.type == "html_inline":
                    html_table_tags += len(re.findall(r"<table\b", child.content, re.IGNORECASE))
    return {
        "paragraphs": paragraphs,
        "table_count": table_count,
        "html_table_tags": html_table_tags,
        "images": images,
        "source": source,
    }



def count_tables_pandoc(md_path):
    result = subprocess.run(
        [
            "pandoc",
            str(md_path),
            "-f", "gfm" if md_path == DOCLING_MD else "markdown+grid_tables",
            "-t", "html",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    return result.stdout.count("<table")


def compare_paragraphs(original, converted):
    candidates = [normalize(p) for p in converted if normalize(p)]
    joined = " ".join(candidates)
    suspicious = []
    for index, paragraph in enumerate(original):
        query = normalize(plain_markdown(paragraph))
        if not query:
            continue
        if query in joined:
            continue
        match = process.extractOne(query, candidates, scorer=fuzz.ratio)
        score = round(match[1], 1) if match else 0
        if score < SIMILARITY_THRESHOLD:
            suspicious.append({"index": index, "similarity": score, "text": paragraph[:500]})
    return suspicious


def check_images(md_path, images):
    missing, external, found = [], [], []
    for image in images:
        if not image:
            continue
        parsed = urlparse(image)
        if parsed.scheme not in ("", "file") or image.startswith("//"):
            external.append(image)
            continue
        if parsed.scheme == "file":
            target = Path(unquote(parsed.path))
        else:
            raw = Path(unquote(parsed.path))
            target = raw if raw.is_absolute() else md_path.parent / raw
        if target.is_file():
            found.append(str(target))
        else:
            missing.append({"reference": image, "resolved_path": str(target)})
    return {"found": found, "missing": missing, "external": external}


def validate(name, odt, md_path):
    md = read_markdown(md_path)
    md["pandoc_table_count"] = count_tables_pandoc(md_path)
    suspicious = compare_paragraphs(odt["paragraphs"], md["paragraphs"])
    images = check_images(md_path, md["images"])
    report = {
        "converter": name,
        "source": str(INPUT),
        "markdown": str(md_path),
        "original_text_blocks": len(odt["paragraphs"]),
        "converted_text_blocks": len(md["paragraphs"]),
        "potentially_unmatched_text_blocks": suspicious,
        "original_tables": odt["table_count"],
        "recognized_tables_pandoc": md["pandoc_table_count"],
        "recognized_pipe_tables": md["table_count"],
        "recognized_html_table_tags": md["html_table_tags"],
        "table_detection_warning": (
            "markdown-it-py detects pipe tables only; "
            "Pandoc additionally detects grid tables. "
            "Cell contents and merged cells are not yet validated."
            ),
        "original_image_references": odt["image_count"],
        "markdown_image_references": len(md["images"]),
        "found_image_files": len(images["found"]),
        "missing_image_files": images["missing"],
        "external_image_references": images["external"],
        "manual_review_required": [
            "Verify the content and layout of all tables against the ODT",
            "Verify formulas and formula images",
            "Verify provision numbers, dates, percentages and coefficients",
            "Check flagged text blocks against the original",
        ],
    }
    path = OUTPUT / f"{name}-conversion-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\n{name.upper()}: "
        f"flagged blocks {len(suspicious)}, "
        f"pipe tables {md['table_count']}, "
        f"Pandoc tables {md['pandoc_table_count']}, "
        f"image references {len(md['images'])}, "
        f"missing files {len(images['missing'])}"
        )
    print(f"Report: {path}")
    return {
        "converter": name,
        "potentially_unmatched_text_blocks": len(suspicious),
        "recognized_pipe_tables": md["table_count"],
        "markdown_image_references": len(md["images"]),
        "missing_image_files": len(images["missing"]),
        "recognized_tables_pandoc": md["pandoc_table_count"],
    }


def main():
    for path in (INPUT, DOCLING_MD, PANDOC_MD):
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    print("Reading source ODT...")
    odt = read_odt(INPUT)
    docling = validate("docling", odt, DOCLING_MD)
    pandoc = validate("pandoc", odt, PANDOC_MD)
    comparison = {
        "source": str(INPUT),
        "docling": docling,
        "pandoc": pandoc,
        "note": "These metrics are diagnostic; low similarity does not prove text loss.",
    }
    target = OUTPUT / "comparison-report.json"
    target.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSummary report: {target}")


if __name__ == "__main__":
    main()
