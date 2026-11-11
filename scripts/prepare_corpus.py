"""№ 10: исходные ODT → документы, структурные единицы, чанки и ссылки JSONL."""

import csv
import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import unquote
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw"
OUT = ROOT / "data/processed/corpus"
NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
}
MONTHS = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]
HEADER = re.compile(
    r"^(Федеральный закон|Положение|Указание|Инструкция|Постановление|Приказ)"
    r"(?: (Банка России|ЦБР|Правительства РФ|Минфина России))? от "
    r"(\d{1,2}) ([а-я]+) (\d{4}) г(?:\.|ода)?\s*(?:N|№)\s*"
    r"(\d+(?:\s*[-‐‑–]\s*[А-ЯA-Z]+|[а-я]+)?)\b"
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def clean(text):
    text = unicodedata.normalize("NFC", text).replace("\u00ad", "")
    text = text.replace("\xa0", " ").replace("\u202f", " ")
    text = re.sub(r"(?<=\d)[‐‑](?=[ПИУ]|ФЗ)", "-", text)
    return "\n".join(
        re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()
    ).strip()


def metadata(title):
    match = HEADER.match(clean(title))
    if not match:
        return {
            "document_id": "title:" + sha(title.casefold().encode())[:16],
            "number": None,
            "document_date": None,
            "title": title,
        }
    kind, issuer, day, month, year, number = match.groups()
    issuer = "Банка России" if issuer == "ЦБР" else issuer or "Российская Федерация"
    number = re.sub(r"\s*[-‐‑–]\s*", "-", number).upper()
    issued = date(int(year), MONTHS.index(month) + 1, int(day)).isoformat()
    return {
        "document_id": f"{issuer}|{kind}|{issued}|{number}",
        "number": number,
        "document_date": issued,
        "title": title,
    }


def read_odt(path):
    """Абзацы, заголовки и таблицы без повторного обхода вложенных таблиц."""
    with ZipFile(path) as archive:
        content = ET.fromstring(archive.read("content.xml"))
        styles = {}
        for root in [ET.fromstring(archive.read("styles.xml")), content]:
            for node in root.findall(".//style:style", NS):
                props = node.find("style:text-properties", NS)
                styles[node.get(f"{{{NS['style']}}}name")] = (
                    node.get(f"{{{NS['style']}}}parent-style-name"),
                    props.get(f"{{{NS['style']}}}text-position", "")
                    if props is not None
                    else "",
                )
        positions = {}
        for name in styles:
            parent, seen = name, set()
            while parent in styles and parent not in seen:
                seen.add(parent)
                parent, position = styles[parent]
                if position:
                    positions[name] = position
                    break
        images = {}

        def render(node):
            tag = node.tag.split("}")[-1]
            if tag == "s":
                return " " * int(node.get(f"{{{NS['text']}}}c", "1"))
            if tag in {"tab", "line-break"}:
                return "\t" if tag == "tab" else "\n"
            if tag == "image":
                member = unquote(node.get(f"{{{NS['xlink']}}}href")).removeprefix("./")
                data = archive.read(member)
                name = sha(data) + Path(member).suffix.lower()
                images[name] = data
                return f"[image:media/{name}]"
            text = (node.text or "") + "".join(
                render(child) + (child.tail or "") for child in node
            )
            position = positions.get(node.get(f"{{{NS['text']}}}style-name"), "")
            if tag == "span" and text.strip() and position.startswith(("super", "sub")):
                return (
                    ("^{" if position.startswith("super") else "_{")
                    + text.strip()
                    + "}"
                )
            return text

        def elements(node):
            for child in node:
                if child.tag in {
                    f"{{{NS['text']}}}p",
                    f"{{{NS['text']}}}h",
                    f"{{{NS['table']}}}table",
                }:
                    yield child
                else:
                    yield from elements(child)

        blocks, pending, empty = [], [], 0
        body = content.find(".//office:text", NS)
        for index, node in enumerate(elements(body)):
            tag = node.tag.split("}")[-1]
            table = []
            if tag == "table":
                for row in node.findall(".//table:table-row", NS):
                    cells = []
                    for cell in row:
                        cells.append(
                            {
                                "text": clean("\n".join(render(c) for c in cell)),
                                "attributes": {
                                    k.split("}")[-1]: v for k, v in cell.attrib.items()
                                },
                                "covered": cell.tag.endswith("}covered-table-cell"),
                            }
                        )
                    table.append(
                        {
                            "cells": cells,
                            "repeat": int(
                                row.get(f"{{{NS['table']}}}number-rows-repeated", "1")
                            ),
                        }
                    )
                text = "\n".join(
                    " | ".join(c["text"].replace("\n", " / ") for c in r["cells"])
                    for r in table
                )
            else:
                text = render(node)
            anchors = [
                a.get(f"{{{NS['text']}}}name")
                for a in node.iter()
                if a.tag.split("}")[-1] in {"bookmark", "bookmark-start"}
            ]
            pending.extend(a for a in anchors if a)
            text = clean(text)
            if not text:
                empty += 1
                continue
            links = [
                {
                    "url": a.get(f"{{{NS['xlink']}}}href", ""),
                    "text": clean("".join(a.itertext())),
                }
                for a in node.findall(".//text:a", NS)
            ]
            blocks.append(
                {
                    "source_block": index,
                    "kind": tag,
                    "style": node.get(f"{{{NS['text']}}}style-name", ""),
                    "text": text,
                    "links": links,
                    "anchors": pending,
                    "table": table,
                }
            )
            pending = []
        if pending and blocks:
            blocks[-1]["anchors"].extend(pending)
        return {"blocks": blocks, "images": images, "empty_blocks": empty}


def inventory():
    """Один формат источников; карточки и дубли не попадают в текстовый корпус."""
    rows, sources, fingerprints, versions = [], {}, {}, defaultdict(set)
    for path in sorted(RAW.glob("*.odt"), key=lambda p: (p.stem != "845_p", p.name)):
        doc = read_odt(path)
        sources[path.stem] = doc
        text = "\n\n".join(b["text"] for b in doc["blocks"])
        meta = metadata(doc["blocks"][0]["text"] if doc["blocks"] else "")
        body = [b["text"] for b in doc["blocks"][1:]]
        card = (
            bool(body)
            and any(t.startswith("Регистрационный N") for t in body)
            and all(
                re.match(
                    r"^(Зарегистрировано|Регистрационный N|Вступает в силу|"
                    r"Опубликование:|сайт Банка России|Вестник Банка России)",
                    t,
                )
                for t in body
            )
        )
        role = (
            "registration_card"
            if card
            else "navigation"
            if meta["title"] == "Курсы валют"
            else "act"
            if meta["number"]
            else "reference"
        )
        fingerprint = sha(text.encode())
        canonical = fingerprints.setdefault(fingerprint, path.stem)
        if role == "act":
            versions[meta["document_id"]].add(fingerprint)
        rows.append(
            {
                **meta,
                "file_id": path.stem,
                "source_file": f"data/raw/{path.name}",
                "source_sha256": sha(path.read_bytes()),
                "edition_date": None,
                "text_sha256": fingerprint,
                "canonical_file_id": canonical,
                "role": role,
                "selected": canonical == path.stem
                and role not in {"registration_card", "navigation"},
                "bytes": path.stat().st_size,
                "chars": len(text),
                "empty_blocks": doc["empty_blocks"],
            }
        )
    for row in rows:
        row["edition_conflict"] = len(versions[row["document_id"]]) > 1
        row["selected"] &= not row["edition_conflict"]
    return rows, sources


def make_units(blocks, doc):
    """Иерархия и продолжения пунктов, как у команды; дополнено статьями."""
    units, headings, known, repeats = [], {}, {}, defaultdict(int)
    chapter = appendix = section = article = None
    active, pending_anchors = None, []
    editorial_styles = {
        b["style"].removesuffix("header")
        for b in blocks
        if b["text"] in {"ГАРАНТ:", "Информация об изменениях:"}
    }
    notes = False
    for block in blocks:
        text, kind = block["text"], block["kind"]
        editorial = block["style"].removesuffix("header") in editorial_styles
        app = re.match(r"^Приложение\s*(?:N|№)?\s*(\d+)\b", text)
        chap = re.match(r"^Глава\s+([\dIVXLCDM.]+)\b", text) if kind == "h" else None
        sec = re.match(r"^Раздел\s+([\dIVXLCDM.]+)\b", text) if kind == "h" else None
        article_text = re.sub(r"(?<=\d)\^\{(\d+)\}", r".\1", text)
        art = re.match(r"^Статья\s+(\d+(?:\.\d+)*)\.\s", article_text)
        separator = bool(re.fullmatch(r"[─━]{5,}", text))
        # Перечни «1) … 2) …» остаются внутри пункта, а не становятся его дублями.
        point = re.match(r"^(\d+(?:\.\d+)*)\.\s", text)
        # 15.8.1 в исходнике без завершающей точки; IFRS также имеет номера без точки.
        point = point or re.match(r"^(\d+(?:\.\d+)+)\s", text)
        if doc["role"] == "reference":
            point = point or re.match(r"^(\d+)\s", text)
        typ, number = "context", None
        if not editorial and (app or chap or sec or art or kind == "h"):
            notes = False
        if editorial:
            typ = "editorial"
        elif separator:
            notes = not notes
            typ = "service"
        elif notes:
            note = re.match(r"^(?:\^\{(\d+)\}|(\d+)\s)", text)
            if note:
                number, typ = note[1] or note[2], "footnote"
        elif app:
            appendix, chapter, section, article = app[1], None, None, None
            headings = {}
            typ = "appendix"
        elif sec:
            section, chapter, article = sec[1].rstrip("."), None, None
            headings = {k: v for k, v in headings.items() if k == "appendix"}
            typ = "section"
        elif chap:
            chapter, article = chap[1].rstrip("."), None
            headings = {
                k: v for k, v in headings.items() if k in {"appendix", "section"}
            }
            typ = "chapter"
        elif art:
            article, number, typ = art[1], art[1], "article"
            headings.pop("article", None)
        elif kind == "h":
            typ = "heading"
            known = {}
        elif point:
            number = point[1]
            typ = (
                "subpoint"
                if len(number.split(".")) > (1 if appendix or article else 2)
                else "point"
            )
        boundary = (
            active is None
            or number
            or typ
            in {"heading", "chapter", "section", "appendix", "service", "editorial"}
            or active["type"]
            in {"heading", "chapter", "section", "appendix", "service"}
        )
        if editorial:
            boundary = not units or units[-1]["type"] != "editorial"
        if boundary:
            scope = (
                "footnotes" if notes else f"appendix-{appendix}" if appendix else "main"
            )
            label = f"article-{article}:" if article and typ != "article" else ""
            label += ("article-" if typ == "article" else "") + (
                number or f"block-{block['source_block']}"
            )
            key = f"{doc['file_id']}:{scope}:{label}"
            repeats[key] += 1
            uid = key if repeats[key] == 1 else f"{key}:occurrence-{repeats[key]}"
            parent = next(reversed(headings.values()))[0] if headings else None
            if typ in {"point", "subpoint"}:
                parent = known.get((scope, article, "article"), parent)
                parent = (
                    known.get((scope, article, number.rsplit(".", 1)[0]), parent)
                    if "." in number
                    else parent
                )
                parent = parent or known.get((scope, article, "article"))
                known[(scope, article, number)] = uid
            elif typ == "article":
                known[(scope, article, "article")] = uid
            units.append(
                {
                    "id": uid,
                    "document_id": doc["document_id"],
                    "file_id": doc["file_id"],
                    "type": typ,
                    "number": number,
                    "parent_id": parent,
                    "chapter": chapter,
                    "appendix": appendix,
                    "section": section,
                    "article": article,
                    "headings": [v[1] for v in headings.values()],
                    "text": "",
                    "anchors": [],
                    "anchor_spans": {},
                    "links": [],
                    "tables": [],
                    "source_blocks": [],
                }
            )
            if typ in {"chapter", "section", "appendix", "heading"} or typ == "article":
                headings[typ] = (uid, text)
            if not editorial:
                active = units[-1]
        unit = units[-1] if editorial else active
        # Вставки отдельны; нормативное продолжение возвращается в свой пункт.
        # Закладка перед примечанием переносится к следующему нормативному блоку.
        block_anchors = block["anchors"]
        if editorial:
            pending_anchors.extend(block_anchors)
            block_anchors = []
        else:
            block_anchors = pending_anchors + block_anchors
            pending_anchors = []
        start = len(unit["text"]) + (2 if unit["text"] else 0)
        unit["text"] += ("\n\n" if unit["text"] else "") + text
        unit["source_blocks"].append(block["source_block"])
        unit["anchors"].extend(block_anchors)
        for anchor in block_anchors:
            old = unit["anchor_spans"].get(anchor, [start, start])
            unit["anchor_spans"][anchor] = [min(start, old[0]), len(unit["text"])]
        unit["links"].extend(block["links"])
        if block["table"]:
            unit["tables"].append(block["table"])
    for unit in units:
        unit["heading_only"] = unit["type"] in {
            "heading",
            "chapter",
            "section",
            "appendix",
        } or (unit["type"] == "article" and len(unit["source_blocks"]) == 1)
    if pending_anchors:
        units[-1]["anchors"].extend(pending_anchors)
        units[-1]["anchor_spans"].update(
            {a: [0, len(units[-1]["text"])] for a in pending_anchors}
        )
    return units


def resolve_links(documents, units):
    files = {d["file_id"]: d["document_id"] for d in documents}
    selected = {d["document_id"] for d in documents if d["selected"]}
    anchors = defaultdict(list)
    for unit in units:
        for anchor, span in unit["anchor_spans"].items():
            anchors[unit["document_id"], anchor].append((unit["id"], span))
    result = []
    for unit in units:
        for link in unit["links"]:
            url = link["url"]
            redirect = re.search(r"/document/redirect/(\d+)(?:/([^?#/]+))?", url)
            target_doc = (
                unit["document_id"]
                if url.startswith("#")
                else files.get(redirect[1])
                if redirect
                else None
            )
            anchor = (
                unquote(url[1:])
                if url.startswith("#")
                else "anchor" + unquote(redirect[2])
                if redirect and redirect[2] not in {None, "0"}
                else None
            )
            targets = anchors.get((target_doc, anchor), []) if anchor else []
            status = (
                "resolved"
                if len(targets) == 1
                else "ambiguous_anchor"
                if targets
                else "anchor_missing"
                if anchor and target_doc in selected
                else "document_only"
                if target_doc in selected
                else "document_not_indexed"
                if target_doc
                else "target_missing"
                if redirect
                else "external_url"
            )
            result.append(
                {
                    "source_unit_id": unit["id"],
                    "source_document_id": unit["document_id"],
                    **link,
                    "target_document_id": target_doc,
                    "target_anchor": anchor,
                    "target_unit_id": targets[0][0] if len(targets) == 1 else None,
                    "target_span": targets[0][1] if len(targets) == 1 else None,
                    "status": status,
                }
            )
    return result


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    from chunk_corpus import make_chunks

    documents, sources = inventory()
    statuses = {}
    status_path = ROOT / "data/legal_status.csv"
    if status_path.exists():
        with status_path.open() as file:
            statuses = {r["document_id"]: r for r in csv.DictReader(file)}
    units = []
    (OUT / "media").mkdir(parents=True, exist_ok=True)
    for doc in documents:
        doc["legal_status"] = statuses.get(doc["document_id"], {"status": "unknown"})
        if not doc["selected"]:
            continue
        source = sources[doc["file_id"]]
        units.extend(make_units(source["blocks"], doc))
        for name, data in source["images"].items():
            (OUT / "media" / name).write_bytes(data)
    chunks = make_chunks(units, documents)
    links = resolve_links(documents, units)
    by_unit = defaultdict(list)
    for chunk in chunks:
        by_unit[chunk["unit_id"]].append(chunk)
    for link in links:
        span = link["target_span"]
        link["target_chunk_ids"] = [
            c["id"]
            for c in by_unit[link["target_unit_id"]]
            if span and c["start"] < span[1] and c["end"] > span[0]
        ]
    for name, rows in [
        ("documents", documents),
        ("units", units),
        ("chunks", chunks),
        ("links", links),
    ]:
        write_jsonl(OUT / f"{name}.jsonl", rows)
    print(
        f"Источников: {sum(d['selected'] for d in documents)}; единиц: {len(units)}; "
        f"чанков: {len(chunks)}; ссылок: {len(links)}"
    )


if __name__ == "__main__":
    main()
