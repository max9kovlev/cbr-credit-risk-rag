"""EDA отдельно для 845-П и всего корпуса; таблицы для одного небольшого ноутбука."""

import json
import re
from collections import Counter

import pandas as pd
from chunk_corpus import STRUCTURAL, load_tokenizer
from prepare_corpus import OUT, ROOT


def words(text):
    return re.findall(r"[^\W\d_]+", re.sub(r"\[image:[^\]]+\]", " ", text).casefold())


def read(name):
    return [
        json.loads(line) for line in (OUT / f"{name}.jsonl").read_text().splitlines()
    ]


def main():
    documents, units, chunks, links = (
        read(n) for n in ("documents", "units", "chunks", "links")
    )
    tokenizer = load_tokenizer()
    tokens = {u["id"]: len(tokenizer.encode(u["text"]).ids) for u in units}
    registry = {d["document_id"]: d for d in documents if d["selected"]}
    root = ROOT / "data/eda"
    root.mkdir(exist_ok=True)
    # Этот реестр также служит входом обновления юридических статусов.
    pd.json_normalize(documents).to_csv(root / "documents.csv", index=False)
    for scope in ("main", "all"):
        directory = root / scope
        directory.mkdir(exist_ok=True)
        docs = [
            d for d in registry.values() if scope == "all" or d["file_id"] == "845_p"
        ]
        selected = {d["document_id"] for d in docs}
        us = [u for u in units if u["document_id"] in selected]
        cs = [c for c in chunks if c["document_id"] in selected]
        ls = [link for link in links if link["source_document_id"] in selected]
        frequencies, acts = Counter(), Counter()
        for u in us:
            frequencies.update(words(u["text"]))
            acts.update(
                f"{n}-{suffix.upper()}"
                for n, suffix in re.findall(
                    r"(?:№|\bN)\s*(\d{1,6})\s*[-‐‑–]\s*(ФЗ|П|И|У)\b", u["text"], re.I
                )
            )
        lengths = [
            {
                k: u[k]
                for k in (
                    "id",
                    "file_id",
                    "type",
                    "chapter",
                    "appendix",
                    "article",
                    "number",
                )
            }
            | {
                "chars": len(u["text"]),
                "words": len(words(u["text"])),
                "tokens": tokens[u["id"]],
                "indexable": u["type"] not in STRUCTURAL and not u["heading_only"],
                "tables": len(u["tables"]),
            }
            for u in us
        ]
        stats = pd.DataFrame(lengths)
        df = pd.json_normalize(docs)
        df["words"] = [
            sum(
                len(words(u["text"]))
                for u in us
                if u["document_id"] == d["document_id"]
            )
            for d in docs
        ]
        df.to_csv(directory / "documents.csv", index=False)
        stats.to_csv(directory / "unit_lengths.csv", index=False)
        pd.DataFrame(
            [{k: c[k] for k in ("id", "unit_id", "tokens", "start", "end")} for c in cs]
        ).to_csv(directory / "chunk_lengths.csv", index=False)
        for name, counts in [("words", frequencies), ("acts", acts)]:
            pd.DataFrame(counts.most_common(), columns=["term", "count"]).to_csv(
                directory / f"{name}.csv", index=False
            )
        pd.DataFrame(ls).groupby("status", dropna=False).size().rename("count").to_csv(
            directory / "link_statuses.csv"
        )
        anomalies = []
        for d in documents:
            if scope == "main" and d["document_id"] not in selected:
                continue
            for issue, detail in [
                (
                    "duplicate",
                    d["canonical_file_id"]
                    if d["file_id"] != d["canonical_file_id"]
                    else None,
                ),
                (
                    "excluded_role",
                    d["role"]
                    if d["role"] in {"navigation", "registration_card"}
                    else None,
                ),
                (
                    "edition_conflict",
                    d["document_id"] if d["edition_conflict"] else None,
                ),
                (
                    "status_unknown",
                    d["number"] or d["file_id"]
                    if d["legal_status"]["status"] == "unknown"
                    else None,
                ),
            ]:
                if detail is not None:
                    anomalies.append(
                        {"source": d["file_id"], "issue": issue, "detail": detail}
                    )
        for link in ls:
            if link["status"] not in {"resolved", "document_only", "external_url"}:
                anomalies.append(
                    {
                        "source": link["source_unit_id"],
                        "issue": link["status"],
                        "detail": link["url"],
                    }
                )
        for u in us:
            if ":occurrence-" in u["id"]:
                anomalies.append(
                    {
                        "source": u["id"],
                        "issue": "repeated_number",
                        "detail": u["number"],
                    }
                )
        pd.DataFrame(anomalies, columns=["source", "issue", "detail"]).to_csv(
            directory / "anomalies.csv", index=False
        )
        summary = {
            "documents": len(docs),
            "acts": sum(d["role"] == "act" for d in docs),
            "units": len(us),
            "chunks": len(cs),
            "max_tokens": max(c["tokens"] for c in cs),
            "word_occurrences": frequencies.total(),
            "word_forms": len(frequencies),
            "hyperlinks": len(ls),
            "links_resolved_to_unit": sum(link["status"] == "resolved" for link in ls),
            "links_resolved_to_chunks": sum(
                bool(link["target_chunk_ids"]) for link in ls
            ),
            "legal_status": dict(Counter(d["legal_status"]["status"] for d in docs)),
        }
        (directory / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
        )
        print(scope, summary)


if __name__ == "__main__":
    main()
