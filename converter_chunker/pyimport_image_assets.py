"""Validation and text serialization of embedded document pictures."""


def missing_descriptions(document):
    return [
        picture.self_ref for picture in document.pictures
        if not (picture.meta and picture.meta.description and picture.meta.description.text.strip())
    ]


def image_text_serializer_provider():
    """Explicitly emit picture descriptions at their document position, exactly once."""
    from docling_core.transforms.chunker.hierarchical_chunker import ChunkingDocSerializer
    from docling_core.transforms.serializer.base import BaseSerializerProvider
    from docling_core.transforms.serializer.common import create_ser_result
    from docling_core.transforms.serializer.markdown import (
        MarkdownPictureSerializer, MarkdownMetaSerializer,
    )
    from docling_core.types.doc import PictureItem

    class PictureTextSerializer(MarkdownPictureSerializer):
        def serialize(self, *, item, doc_serializer, doc, **kwargs):
            description = item.meta.description if item.meta else None
            if not description or not description.text.strip():
                raise ValueError(f"Image description is missing: {item.self_ref}")
            # Captions and other picture content remain in their normal position.
            result = super().serialize(item=item, doc_serializer=doc_serializer, doc=doc, **kwargs)
            return create_ser_result(
                text="\n\n".join(part for part in (result.text, description.text.strip()) if part),
                span_source=[result, create_ser_result(text=description.text.strip(), span_source=item)],
            )

    class PictureMetaSerializer(MarkdownMetaSerializer):
        def serialize(self, *, item, doc, **kwargs):
            if isinstance(item, PictureItem):
                kwargs["blocked_meta_names"] = list(set(kwargs.get("blocked_meta_names") or []) | {"description"})
            return super().serialize(item=item, doc=doc, **kwargs)

    class Provider(BaseSerializerProvider):
        def get_serializer(self, doc):
            return ChunkingDocSerializer(
                doc=doc, picture_serializer=PictureTextSerializer(),
                meta_serializer=PictureMetaSerializer(),
            )

    return Provider()
