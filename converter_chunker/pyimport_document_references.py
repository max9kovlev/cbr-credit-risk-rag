"""Preserve ODT bookmarks and hyperlinks through Docling using temporary markers."""

import re
import uuid
from collections import defaultdict
from urllib.parse import unquote
from zipfile import ZipFile
import xml.etree.ElementTree as ET

TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
XLINK = "http://www.w3.org/1999/xlink"


def prepare_odt(source, destination):
    """Write a marked copy; return a registry without modifying the original."""
    prefix = "RAGREF" + uuid.uuid4().hex
    registry = []
    with ZipFile(source) as archive:
        root = ET.fromstring(archive.read("content.xml"))
        for element in list(root.iter()):
            record = None
            if element.tag in {f"{{{TEXT}}}bookmark", f"{{{TEXT}}}bookmark-start"}:
                record = {"kind": "anchor", "name": element.get(f"{{{TEXT}}}name", "")}
            elif element.tag == f"{{{TEXT}}}a":
                record = {
                    "kind": "link", "text": "".join(element.itertext()),
                    "url": element.get(f"{{{XLINK}}}href", ""),
                }
            if record is None:
                continue
            marker = f"{prefix}X{len(registry)}END"
            record.update(marker=marker, item_refs=[])
            registry.append(record)
            if record["kind"] == "anchor":
                element.tail = marker + (element.tail or "")
            else:
                element.text = marker + (element.text or "")
        with ZipFile(destination, "w") as output:
            for info in archive.infolist():
                output.writestr(info, ET.tostring(root, encoding="utf-8", xml_declaration=True)
                                if info.filename == "content.xml" else archive.read(info.filename))
    return registry


def remove_markers(document, registry):
    """Remove markers from all model string fields and record their owning items."""
    lookup = {record["marker"]: record for record in registry}
    if not lookup:
        return
    pattern = re.compile("|".join(map(re.escape, lookup)))

    def clean(value, owner=None):
        if type(value) is str:
            def replace(match):
                refs = lookup[match.group()]["item_refs"]
                if owner and owner not in refs:
                    refs.append(owner)
                return ""
            return pattern.sub(replace, value)
        if isinstance(value, list):
            for index, item in enumerate(value):
                value[index] = clean(item, owner)
            return value
        if isinstance(value, dict):
            for key, item in value.items():
                value[key] = clean(item, owner)
            return value
        if hasattr(type(value), "model_fields"):
            owner = getattr(value, "self_ref", owner)
            for name in type(value).model_fields:
                old = getattr(value, name)
                new = clean(old, owner)
                if new is not old:
                    setattr(value, name, new)
        return value

    clean(document)
    for record in registry:
        del record["marker"]


def resolve_references(chunks, registry, document, id_prefix):
    """Resolve to source-item chunks, retaining explicit unresolved statuses."""
    by_ref = defaultdict(set)
    headings = defaultdict(list)
    for item in document.texts:
        if str(getattr(item, "label", "")) in {"section_header", "title"}:
            headings[item.text].append(item.self_ref)
    for index, chunk in enumerate(chunks):
        chunk_id = f"{id_prefix}-{index:05d}"
        for item in chunk.meta.doc_items:
            by_ref[item.self_ref].add(chunk_id)
        # A heading may appear only in contextual metadata. Match only unique headings.
        for heading in chunk.meta.headings or []:
            if len(headings[heading]) == 1:
                by_ref[headings[heading][0]].add(chunk_id)

    def ids(record):
        return sorted({cid for ref in record["item_refs"] for cid in by_ref[ref]})

    anchors = defaultdict(list)
    for record in registry:
        if record["kind"] == "anchor":
            anchors[record["name"]].append(record)
    anchor_map = {}
    for name, records in anchors.items():
        targets = ids(records[0]) if len(records) == 1 else []
        anchor_map[name] = {
            "chunk_ids": targets,
            "status": "duplicate_anchor" if len(records) > 1 else
                      "resolved" if targets else "target_not_chunked",
        }
    links = []
    for record in registry:
        if record["kind"] != "link":
            continue
        link = {k: v for k, v in record.items() if k != "kind"}
        link["source_chunk_ids"] = ids(record)
        link["target_chunk_ids"] = []
        if record["url"].startswith("#"):
            name = unquote(record["url"][1:])
            target = anchor_map.get(name, {"chunk_ids": [], "status": "anchor_not_found"})
            link.update(type="internal", anchor=name, status=target["status"],
                        target_chunk_ids=target["chunk_ids"])
        else:
            link.update(type="external", status="external_not_loaded")
        links.append(link)
    return {"anchors": anchor_map, "links": links}


def index_references_by_chunk(resolved):
    """Group resolved links and anchor names by chunk ID."""
    links_by_chunk = {}
    anchors_by_chunk = {}
    for link in resolved["links"]:
        for chunk_id in link["source_chunk_ids"]:
            links_by_chunk.setdefault(chunk_id, []).append(link)
    for name, target in resolved["anchors"].items():
        for chunk_id in target["chunk_ids"]:
            anchors_by_chunk.setdefault(chunk_id, []).append(name)
    return links_by_chunk, anchors_by_chunk
