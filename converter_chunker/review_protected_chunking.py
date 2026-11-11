import unittest
from copy import copy
from types import SimpleNamespace

from pyimport_protected_chunking import pack_blocks


class Model(SimpleNamespace):
    def model_copy(self, *, update):
        result = copy(self)
        result.__dict__.update(update)
        return result

    def model_dump(self, *, exclude):
        return {k: v for k, v in vars(self).items() if k not in exclude}


def block(text, ref, heading='Section'):
    return Model(text=text, meta=Model(headings=[heading], doc_items=[Model(self_ref=ref)]))


def contextualize(chunk):
    return ' '.join(chunk.meta.headings) + '\n' + chunk.text


def pack(blocks, limit=512):
    return list(pack_blocks(blocks, contextualize=contextualize,
                           count_tokens=lambda s: len(s.split()), max_tokens=limit))


class ProtectedChunkTests(unittest.TestCase):
    def test_long_paragraph_splits_but_formula_remains_whole(self):
        formula = r'$$ x = a + b + c $$'
        text = ('word ' * 510) + formula
        result = pack([block(text, 'a')])
        self.assertGreater(len(result), 1)
        self.assertEqual(''.join(c.text for c in result), text)
        self.assertTrue(any(formula in c.text for c in result))
        self.assertTrue(all(len(contextualize(c).split()) <= 512 for c in result))
        self.assertTrue(all(c.meta.doc_items[0].self_ref == 'a' for c in result))

    def test_oversized_formula_fails(self):
        with self.assertRaisesRegex(ValueError, 'formula'):
            pack([block('$$ ' + 'x + ' * 300 + 'x $$', 'a')])

    def test_oversized_context_fails(self):
        with self.assertRaisesRegex(ValueError, 'context'):
            pack([block('body', 'a', 'heading ' * 10)], 5)

    def test_sentence_boundaries(self):
        result = pack([block('First sentence. Second sentence. Third sentence.', 'a')], 5)
        self.assertEqual(result[0].text, 'First sentence. Second sentence. ')
        self.assertEqual(result[1].text, 'Third sentence.')

    def test_other_latex_delimiters(self):
        for formula in [r'\[ x + y \]', r'\( x + y \)', r'$x + y$',
                        r'\begin{equation} x + y \end{equation}']:
            result = pack([block('word ' * 8 + formula, 'a')], 8)
            self.assertTrue(any(formula in c.text for c in result))
            self.assertTrue(all(len(contextualize(c).split()) <= 8 for c in result))

    def test_budget_counts_headings_and_preserves_references(self):
        result = pack([block('one two', 'a'), block('three four', 'b')], 5)
        self.assertEqual(len(result), 1)
        self.assertEqual([x.self_ref for x in result[0].meta.doc_items], ['a', 'b'])
        self.assertEqual(len(pack([block('one two', 'a'), block('three four', 'b')], 4)), 2)

    def test_sections_do_not_merge(self):
        self.assertEqual(len(pack([block('one', 'a', 'A'), block('two', 'b', 'B')])), 2)

    def test_unclosed_formula_is_not_split(self):
        with self.assertRaisesRegex(ValueError, 'delimiter'):
            pack([block('word ' * 20 + '$$ x + y', 'a')], 8)

    def test_long_identifier_uses_character_fallback(self):
        result = list(pack_blocks([block('abcdefghij', 'a', '')],
                      contextualize=lambda c: c.text, count_tokens=len, max_tokens=4))
        self.assertEqual(''.join(c.text for c in result), 'abcdefghij')
        self.assertTrue(all(len(c.text) <= 4 for c in result))

    def test_empty_document(self):
        self.assertEqual(pack([]), [])


if __name__ == '__main__':
    unittest.main()
