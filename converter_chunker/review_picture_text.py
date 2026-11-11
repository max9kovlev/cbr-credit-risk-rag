"""Integration checks using real Docling documents, without model downloads."""
import unittest
from copy import deepcopy
from docling_core.types.doc import DoclingDocument, PictureMeta, DescriptionMetaField, DocItemLabel
from docling_core.transforms.chunker.hierarchical_chunker import HierarchicalChunker
from pyimport_image_assets import image_text_serializer_provider
from pyimport_protected_chunking import pack_blocks
from pyimport_chunk_export import build_records, validate_picture_text


class PictureTextTests(unittest.TestCase):
    def make_records(self, description, limit=512):
        document = DoclingDocument(name='fixture')
        document.add_text(label=DocItemLabel.TEXT, text='Before image.')
        picture = document.add_picture()
        picture.meta = PictureMeta(description=DescriptionMetaField(text=description))
        document.add_text(label=DocItemLabel.TEXT, text='After image.')
        structural = HierarchicalChunker(serializer_provider=image_text_serializer_provider())
        count = lambda text: len(text.split())
        chunks = list(pack_blocks(structural.chunk(dl_doc=document),
                      contextualize=structural.contextualize, count_tokens=count, max_tokens=limit))
        records = build_records(chunks, contextualize=structural.contextualize,
                    count_tokens=count, max_tokens=limit, prefix='fixture', source='fixture.odt',
                    image_refs={picture.self_ref}, links_by_chunk={}, anchors_by_chunk={})
        return document, records

    def test_description_once_in_position_with_picture_ref(self):
        description = r'Formula $$K = \frac{R}{C}$$'
        document, records = self.make_records(description)
        validate_picture_text(document, records)
        text = '\n'.join(r['text'] for r in records)
        self.assertEqual(text.count(description), 1)
        self.assertLess(text.index('Before'), text.index(description))
        self.assertLess(text.index(description), text.index('After'))
        self.assertEqual(records[0]['images'], [{'ref': document.pictures[0].self_ref}])

    def test_long_description_is_split_and_formula_stays_linked(self):
        document, records = self.make_records('word ' * 600 + '$$x = 2$$')
        validate_picture_text(document, records)
        self.assertTrue(all(r['tokens'] <= 512 for r in records))
        self.assertGreater(len(records), 1)

    def test_missing_body_or_wrong_reference_fails(self):
        document, records = self.make_records('Formula $$x = 2$$')
        missing = deepcopy(records)
        missing[0]['text'] = missing[0]['embedding_text'] = 'Lost'
        with self.assertRaises(ValueError):
            validate_picture_text(document, missing)
        wrong = deepcopy(records)
        wrong[0]['source_item_refs'] = ['#/pictures/999']
        with self.assertRaises(ValueError):
            validate_picture_text(document, wrong)

    def test_contextualization_loss_fails(self):
        document, records = self.make_records('Formula $$x = 2$$')
        records[0]['embedding_text'] = 'Only a heading'
        with self.assertRaises(ValueError):
            validate_picture_text(document, records)


if __name__ == '__main__':
    unittest.main()
