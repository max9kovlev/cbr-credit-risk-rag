"""Validate actual pipeline artifacts, without generating test fixtures."""
import json
import re
from collections import Counter
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from pyimport_pipeline_config import (
    SOURCE, DOCUMENT_JSON, LATEX_JSON, REFERENCES_JSON, CHUNKS_JSONL,
    RESOLVED_REFERENCES_JSON, TOKENIZER_MODEL, MAX_TOKENS,
)
from pyimport_protected_chunking import MATH


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def all_refs(value):
    refs = set()
    if isinstance(value, dict):
        if 'self_ref' in value:
            refs.add(value['self_ref'])
        for child in value.values():
            refs.update(all_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(all_refs(child))
    return refs


def check_document(path, errors):
    from docling_core.types.doc import DoclingDocument
    raw = read_json(path)
    document = DoclingDocument.model_validate(raw)
    if not document.texts and not document.tables:
        errors.append(f'{path.name}: no text or tables')
    if re.search(r'RAGREF[0-9a-f]{32}X\d+END', json.dumps(raw)):
        errors.append(f'{path.name}: temporary markers remain')
    for picture in document.pictures:
        if picture.get_image(document) is None:
            errors.append(f'{picture.self_ref}: embedded image missing or unreadable')
    return raw, document


def validate_conversion(errors):
    raw, document = check_document(DOCUMENT_JSON, errors)
    registry = read_json(REFERENCES_JSON)
    refs = all_refs(raw)
    for index, record in enumerate(registry):
        if not record.get('item_refs') or not set(record['item_refs']) <= refs:
            errors.append(f'references[{index}]: missing or invalid item_refs')
    text_ns = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
    draw_ns = 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0'
    with ZipFile(SOURCE) as archive:
        root = ET.fromstring(archive.read('content.xml'))
    expected = Counter()
    for node in root.iter():
        if node.tag in {f'{{{text_ns}}}bookmark', f'{{{text_ns}}}bookmark-start'}:
            expected[('anchor', node.get(f'{{{text_ns}}}name', ''))] += 1
        if node.tag == f'{{{text_ns}}}a':
            expected[('link', node.get('{http://www.w3.org/1999/xlink}href', ''))] += 1
    actual = Counter((r['kind'], r.get('name') if r['kind'] == 'anchor' else r.get('url')) for r in registry)
    if actual != expected:
        errors.append('ODT links/bookmarks do not match the extracted registry')
    image_count = sum(n.tag == f'{{{draw_ns}}}image' for n in root.iter())
    if len(document.pictures) != image_count:
        errors.append(f'ODT image count {image_count} != extracted {len(document.pictures)}; inspect coverage')
    return {'pictures': len(document.pictures), 'references': len(registry), 'texts': len(document.texts)}


def validate_latex(errors):
    before = read_json(DOCUMENT_JSON)
    after, document = check_document(LATEX_JSON, errors)
    for key in ('texts', 'tables', 'groups', 'body', 'furniture'):
        if before.get(key) != after.get(key):
            errors.append(f'LaTeX stage changed original document structure/text: {key}')
    original = {p['self_ref']: p for p in before.get('pictures', [])}
    if set(original) != {p.self_ref for p in document.pictures}:
        errors.append('LaTeX stage changed image IDs or image count')
    formulas = 0
    for picture in document.pictures:
        ref = picture.self_ref
        description = picture.meta.description if picture.meta else None
        if not description or not description.text.strip():
            errors.append(f'{ref}: description is empty')
            continue
        text = description.text
        formulas += len(list(MATH.finditer(text)))
        remainder = MATH.sub('', text)
        if re.search(r'(?<!\\)\$|\\[\[\(]|\\(?:begin|end)\{', remainder):
            errors.append(f'{ref}: unclosed/unsupported LaTeX delimiter: {remainder[:180]!r}')
    return {'pictures': len(document.pictures), 'recognized_formula_spans': formulas,
            'note': 'Checks preservation and delimiters, not mathematical recognition accuracy'}


def validate_chunks(errors):
    from docling_core.types.doc import DoclingDocument
    from transformers import AutoTokenizer
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
    from pyimport_chunk_export import validate_picture_text
    raw = read_json(LATEX_JSON)
    document = DoclingDocument.model_validate(raw)
    records = [json.loads(line) for line in CHUNKS_JSONL.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not records:
        errors.append('No chunks were written')
        return {'chunks': 0}
    tokenizer = HuggingFaceTokenizer(tokenizer=AutoTokenizer.from_pretrained(TOKENIZER_MODEL, local_files_only=True), max_tokens=MAX_TOKENS)
    ids = [r['id'] for r in records]
    if len(set(ids)) != len(ids):
        errors.append('Duplicate chunk IDs')
    refs = all_refs(raw)
    for index, record in enumerate(records):
        name = record['id']
        tokens = tokenizer.count_tokens(record['embedding_text'])
        if tokens > MAX_TOKENS or tokens != record['tokens']:
            errors.append(f'{name}: token count mismatch or overflow: {tokens}')
        if not record['text'].strip() or record['text'] not in record['embedding_text']:
            errors.append(f'{name}: empty body or lost contextualized text')
        if not record['source_item_refs'] or not set(record['source_item_refs']) <= refs:
            errors.append(f'{name}: invalid source item references')
        if record['previous_chunk_id'] != (ids[index - 1] if index else None):
            errors.append(f'{name}: invalid previous chunk link')
        if record['next_chunk_id'] != (ids[index + 1] if index + 1 < len(ids) else None):
            errors.append(f'{name}: invalid next chunk link')
        for link in record['links']:
            if not set(link['target_chunk_ids']) <= set(ids):
                errors.append(f'{name}: dangling link target')
    try:
        validate_picture_text(document, records)
    except ValueError as error:
        errors.append(str(error))
    from docling_core.transforms.chunker.hierarchical_chunker import HierarchicalChunker
    from pyimport_image_assets import image_text_serializer_provider
    structural = HierarchicalChunker(serializer_provider=image_text_serializer_provider())
    expected_text = ''.join(c.text for c in structural.chunk(dl_doc=document))
    actual_text = ''.join(r['text'] for r in records)
    if ''.join(expected_text.split()) != ''.join(actual_text.split()):
        errors.append('Chunk text is lost, duplicated or reordered relative to serialized document')
    resolved = read_json(RESOLVED_REFERENCES_JSON)
    registry = read_json(REFERENCES_JSON)
    if len(resolved['links']) != sum(r['kind'] == 'link' for r in registry):
        errors.append('Resolved reference report lost source links')
    for link in resolved['links']:
        if not set(link['source_chunk_ids'] + link['target_chunk_ids']) <= set(ids):
            errors.append(f"Reference report contains dangling chunk IDs: {link['url']}")
    for anchor, target in resolved['anchors'].items():
        if not set(target['chunk_ids']) <= set(ids):
            errors.append(f'{anchor}: dangling anchor target')
    return {'chunks': len(records), 'max_tokens': max(r['tokens'] for r in records),
            'unresolved_anchors': sum(v['status'] != 'resolved' for v in resolved['anchors'].values())}
