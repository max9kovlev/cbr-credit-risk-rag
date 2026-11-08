"""№ 10: ODT → очищенный JSONL 845-П. Только стандартная библиотека Python."""

import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from urllib.parse import unquote
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw"
OUT = ROOT / "data/processed/845_p"
NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
}
MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
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
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()).strip()


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
    """Один блок — абзац, заголовок или таблица; вложенные таблицы не обходятся дважды."""
    with ZipFile(path) as archive:
        content = ET.fromstring(archive.read("content.xml"))
        styles = {}
        for root in [ET.fromstring(archive.read("styles.xml")), content]:
            for node in root.findall(".//style:style", NS):
                props = node.find("style:text-properties", NS)
                styles[node.get(f"{{{NS['style']}}}name")] = (
                    node.get(f"{{{NS['style']}}}parent-style-name"),
                    props.get(f"{{{NS['style']}}}text-position", "") if props is not None else "",
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
            text = (node.text or "") + "".join(render(child) + (child.tail or "") for child in node)
            position = positions.get(node.get(f"{{{NS['text']}}}style-name"), "")
            if tag == "span" and text.strip() and position.startswith(("super", "sub")):
                return ("^{" if position.startswith("super") else "_{") + text.strip() + "}"
            return text

        def elements(node):
            for child in node:
                if child.tag in {f"{{{NS['text']}}}p", f"{{{NS['text']}}}h", f"{{{NS['table']}}}table"}:
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
                                "attributes": {k.split("}")[-1]: v for k, v in cell.attrib.items()},
                                "covered": cell.tag.endswith("}covered-table-cell"),
                            }
                        )
                    table.append(
                        {
                            "cells": cells,
                            "repeat": int(row.get(f"{{{NS['table']}}}number-rows-repeated", "1")),
                        }
                    )
                text = "\n".join(
                    " | ".join(c["text"].replace("\n", " / ") for c in r["cells"]) for r in table
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
                {"url": a.get(f"{{{NS['xlink']}}}href", ""), "text": clean("".join(a.itertext()))}
                for a in node.findall(".//text:a", NS)
            ]
            blocks.append(
                {
                    "source_block": index,
                    "kind": tag,
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


def make_units(blocks, meta):
    """Объединяем продолжения пунктов; главы, приложения и сноски сохраняем отдельно."""
    units = []
    chapter = appendix = section = None
    notes = False
    for block in blocks:
        text, kind = block["text"], block["kind"]
        app = re.match(r"^Приложение (\d+)\b", text)
        chap = re.match(r"^Глава (\d+)\.", text) if kind == "h" else None
        sec = re.match(r"^Раздел ([IVXLC]+)\.", text) if kind == "h" else None
        separator = bool(re.fullmatch(r"[─━_\-]{5,}", text))
        if app:
            appendix, chapter, section = int(app[1]), None, None
        elif chap:
            chapter = int(chap[1])
        elif sec:
            section, chapter = sec[1], None
        if separator:
            notes, chapter, appendix, section = True, None, None, None
        number, unit_type = None, "context" if chapter or appendix else "preamble"
        point = re.match(r"^(\d+(?:\.\d+)*)\.\s" if appendix else r"^(\d+(?:\.\d+)+)\.?\s", text)
        if notes:
            note = re.match(r"^(?:\^\{(\d+)\}|(\d+)\s)", text)
            if note:
                number, unit_type = note[1] or note[2], "footnote"
        elif app or kind == "h":
            unit_type = "heading"
        elif point and (appendix or chapter == int(point[1].split(".")[0])):
            number, unit_type = point[1], "clause"
        if separator or text.startswith("Председатель Центрального банка"):
            unit_type = "service"
        if (
            not units
            or number
            or unit_type in {"heading", "service"}
            or units[-1]["type"] in {"heading", "service"}
        ):
            scope = "footnotes" if notes else f"appendix-{appendix}" if appendix else "main"
            label = number or f"block-{block['source_block']}"
            units.append(
                {
                    **meta,
                    "id": f"845p:{scope}:{label}",
                    "type": unit_type,
                    "chapter": chapter,
                    "appendix": appendix,
                    "section": section,
                    "paragraph": number,
                    "text": "",
                    "source_blocks": [],
                    "links": [],
                    "anchors": [],
                    "tables": [],
                }
            )
        unit = units[-1]
        unit["text"] += ("\n\n" if unit["text"] else "") + text
        unit["source_blocks"].append(block["source_block"])
        unit["links"].extend(block["links"])
        unit["anchors"].extend(block["anchors"])
        if block["table"]:
            unit["tables"].append(block["table"])
    return units


def main():
    source = RAW / "845_p.odt"
    document = read_odt(source)
    meta = {
        **metadata(document["blocks"][0]["text"]),
        "edition_date": None,
        "source_file": "data/raw/845_p.odt",
        "source_sha256": sha(source.read_bytes()),
    }
    units = make_units(document["blocks"], meta)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "corpus.jsonl").write_text(
        "".join(json.dumps(u, ensure_ascii=False) + "\n" for u in units), encoding="utf-8"
    )
    (OUT / "media").mkdir(exist_ok=True)
    for name, data in document["images"].items():
        (OUT / "media" / name).write_bytes(data)
    print(f"845-П: {len(units)} записей → {OUT / 'corpus.jsonl'}")


if __name__ == "__main__":
    main()
