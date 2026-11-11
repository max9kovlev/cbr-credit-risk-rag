"""Hierarchy-first chunking with a strict contextual token budget."""

import re


# Preserve complete LaTeX spans, including multi-line display environments.
MATH = re.compile(
    r"(?<!\\)\$\$[\s\S]*?(?<!\\)\$\$"
    r"|(?<!\\)\$(?!\$)(?:\\.|[^$\\])*?(?<!\\)\$"
    r"|\\\[[\s\S]*?\\\]"
    r"|\\\([\s\S]*?\\\)"
    r"|\\begin\{(?P<env>[^}]+)\}[\s\S]*?\\end\{(?P=env)\}"
)


def split_block(block, *, contextualize, count_tokens, max_tokens):
    """Prefer paragraphs, sentences, words, then characters; never split math."""
    def fits(text):
        return count_tokens(contextualize(block.model_copy(update={"text": text}))) <= max_tokens

    if fits(block.text):
        yield block
        return
    if not fits(""):
        raise ValueError("Heading/context alone exceeds the token limit")

    def prose_units(text, level=0):
        if not text:
            return
        if fits(text):
            yield text
            return
        separators = (r"\n\s*\n", r"(?<=[.!?;。！？])\s+", r"\s+")
        if level == len(separators):
            # A long word/identifier: split only outside formula spans.
            for char in text:
                if not fits(char):
                    raise ValueError("No room for text after heading/context")
                yield char
            return
        start = 0
        for match in re.finditer(separators[level], text):
            yield from prose_units(text[start:match.end()], level + 1)
            start = match.end()
        yield from prose_units(text[start:], level + 1)

    # A dedicated formula item can lack Markdown delimiters.
    if block.meta.doc_items and all(
        str(getattr(item, "label", "")) == "formula" for item in block.meta.doc_items
    ):
        raise ValueError("An indivisible formula plus context exceeds the token limit")

    remainder = MATH.sub("", block.text)
    if re.search(r"(?<!\\)\$|\\[\[\(]|\\begin\{", remainder):
        raise ValueError("Unclosed or unsupported LaTeX delimiter in oversized block; cannot split safely")

    def units():
        start = 0
        for match in MATH.finditer(block.text):
            yield from prose_units(block.text[start:match.start()])
            formula = match.group()
            if not fits(formula):
                raise ValueError("An indivisible LaTeX formula plus context exceeds the token limit")
            yield formula
            start = match.end()
        yield from prose_units(block.text[start:])

    pending = ""
    for unit in units():
        if pending and not fits(pending + unit):
            yield block.model_copy(update={"text": pending})
            pending = ""
        pending += unit
    if pending:
        yield block.model_copy(update={"text": pending})



def merge_blocks(left, right):
    """Keep source item references for anchors and images when joining blocks."""
    items = {item.self_ref: item for item in left.meta.doc_items}
    items.update({item.self_ref: item for item in right.meta.doc_items})
    meta = left.meta.model_copy(update={"doc_items": list(items.values())})
    return left.model_copy(update={"text": left.text + "\n\n" + right.text, "meta": meta})


def pack_blocks(blocks, *, contextualize, count_tokens, max_tokens):
    """Split oversized structural blocks, then pack peers within a hard budget."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    pending = None
    parts = (
        part for block in blocks
        for part in split_block(block, contextualize=contextualize,
                                count_tokens=count_tokens, max_tokens=max_tokens)
    )
    for block in parts:
        if pending is None:
            pending = block
            continue
        compatible = (
            pending.meta.model_dump(exclude={"doc_items"})
            == block.meta.model_dump(exclude={"doc_items"})
        )
        if compatible:
            candidate = merge_blocks(pending, block)
            if count_tokens(contextualize(candidate)) <= max_tokens:
                pending = candidate
                continue
        yield pending
        pending = block
    if pending is not None:
        yield pending


def protected_chunks(document, hybrid_chunker, tokenizer, max_tokens):
    """Use Docling structure and contextualization, replacing its hard splitting."""
    from docling_core.transforms.chunker.hierarchical_chunker import HierarchicalChunker

    structural = HierarchicalChunker(serializer_provider=hybrid_chunker.serializer_provider)
    yield from pack_blocks(
        structural.chunk(dl_doc=document),
        contextualize=hybrid_chunker.contextualize,
        count_tokens=tokenizer.count_tokens,
        max_tokens=max_tokens,
    )
