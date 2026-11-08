"""№ 8: размеры, длины, частоты, аномалии и гиперссылки предварительного корпуса."""

import json
import re
from collections import Counter, defaultdict
from urllib.parse import unquote, urlparse

import pandas as pd

from prepare_corpus import OUT, RAW, ROOT, metadata, read_odt, sha

EDA = ROOT / "data/eda"


def words(text):
    text = re.sub(r"\[image:[^\]]+\]", " ", text)
    return re.findall(r"[^\W\d_]+", text.casefold())


def main():
    files = sorted(RAW.glob("*.odt"), key=lambda p: (p.stem != "845_p", p.name))
    documents, sources, anomalies = [], {}, []
    fingerprints, versions = {}, defaultdict(set)
    for path in files:
        doc = read_odt(path)
        blocks = doc["blocks"]
        text = "\n\n".join(b["text"] for b in blocks)
        title = blocks[0]["text"] if blocks else ""
        meta = metadata(title)
        body = [b["text"] for b in blocks[1:]]
        card = (
            bool(body)
            and any(t.startswith("Регистрационный N") for t in body)
            and all(
                re.match(
                    r"^(Зарегистрировано|Регистрационный N|Вступает в силу|Опубликование:|сайт Банка России|Вестник Банка России)",
                    t,
                )
                for t in body
            )
        )
        role = (
            "registration_card"
            if card
            else "navigation"
            if title == "Курсы валют"
            else "act"
            if meta["number"]
            else "reference"
        )
        fingerprint = sha(text.encode())
        canonical = fingerprints.setdefault(fingerprint, path.stem)
        selected = canonical == path.stem and role not in {"navigation", "registration_card"}
        if role == "act":
            versions[meta["document_id"]].add(fingerprint)
        documents.append(
            {
                "file_id": path.stem,
                **meta,
                "role": role,
                "edition_date": None,
                "source_sha256": sha(path.read_bytes()),
                "text_sha256": fingerprint,
                "canonical_file_id": canonical,
                "selected": selected,
                "bytes": path.stat().st_size,
                "chars": len(text),
                "words": len(words(text)),
                "blocks": len(blocks),
                "empty_blocks": doc["empty_blocks"],
                "tables": sum(b["kind"] == "table" for b in blocks),
                "images": sum(b["text"].count("[image:") for b in blocks),
                "links": sum(len(b["links"]) for b in blocks),
            }
        )
        sources[path.stem] = doc
        for code, detail in [
            ("duplicate_text", canonical if canonical != path.stem else None),
            ("non_text_source", role if role in {"navigation", "registration_card"} else None),
            ("empty_document", "Нет текста" if not text else None),
            ("replacement_character", text.count("\ufffd") or None),
        ]:
            if detail is not None:
                anomalies.append({"file_id": path.stem, "issue": code, "detail": detail})
    # Разные тексты одного акта сохраняем для разбора редакций, но не суммируем в частотах.
    for row in documents:
        if len(versions[row["document_id"]]) > 1:
            row["selected"] = False
            anomalies.append(
                {"file_id": row["file_id"], "issue": "edition_conflict", "detail": row["document_id"]}
            )

    frequencies, acts, links = Counter(), Counter(), []
    for row in documents:
        if not row["selected"]:
            continue
        doc = sources[row["file_id"]]
        text = "\n\n".join(b["text"] for b in doc["blocks"])
        frequencies.update(words(text))
        acts.update(
            f"{n}-{suffix.upper()}"
            for n, suffix in re.findall(r"(?:№|\bN)\s*(\d{1,6})\s*[-‐‑–]\s*(ФЗ|П|И|У)\b", text, re.I)
        )
        anchors = {a for b in doc["blocks"] for a in b["anchors"]}
        counts = Counter(link["url"] for b in doc["blocks"] for link in b["links"])
        for url, count in sorted(counts.items()):
            target = re.search(r"/document/redirect/(\d+)", url)
            if url.startswith("#"):
                status = "local_found" if unquote(url[1:]) in anchors else "local_missing"
            elif target:
                status = "target_present" if target[1] in sources else "target_missing"
            else:
                status = "external_url" if urlparse(url).scheme in {"http", "https"} else "other"
            links.append({"file_id": row["file_id"], "url": url, "status": status, "count": count})
            if status.endswith("missing"):
                anomalies.append({"file_id": row["file_id"], "issue": status, "detail": url})

    EDA.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(documents).to_csv(EDA / "documents.csv", index=False)
    pd.DataFrame(links, columns=["file_id", "url", "status", "count"]).to_csv(EDA / "links.csv", index=False)
    pd.DataFrame(anomalies, columns=["file_id", "issue", "detail"]).to_csv(EDA / "anomalies.csv", index=False)
    for name, values in [("word_frequencies", frequencies), ("act_mentions", acts)]:
        pd.DataFrame(values.most_common(), columns=["term", "count"]).to_csv(EDA / f"{name}.csv", index=False)
    units = [json.loads(line) for line in (OUT / "corpus.jsonl").read_text(encoding="utf-8").splitlines()]
    lengths = [
        {
            "id": u["id"],
            "type": u["type"],
            "chapter": u["chapter"],
            "appendix": u["appendix"],
            "paragraph": u["paragraph"],
            "chars": len(u["text"]),
            "words": len(words(u["text"])),
        }
        for u in units
    ]
    pd.DataFrame(lengths).to_csv(EDA / "unit_lengths.csv", index=False)
    summary = {
        "source_files": len(documents),
        "unique_texts": len(fingerprints),
        "acts_by_header": len({r["document_id"] for r in documents if r["number"]}),
        "selected_sources": sum(r["selected"] for r in documents),
        "units_845p": len(units),
        "word_occurrences": frequencies.total(),
        "unique_words": len(frequencies),
        "hyperlink_occurrences": sum(r["count"] for r in links),
        "anomalies": dict(Counter(r["issue"] for r in anomalies)),
    }
    (EDA / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
