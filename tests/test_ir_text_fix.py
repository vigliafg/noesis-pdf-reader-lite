#!/usr/bin/env python3
"""Test del fix grounded per le iniziali in grassetto (mnemoniche/drop-cap)."""

from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import main  # noqa: E402
    _OK = True
except Exception as e:  # noqa: BLE001
    _OK = False
    _ERR = repr(e)


@unittest.skipUnless(_OK, "main non importabile")
class FixBoldInitialsTests(unittest.TestCase):
    VOCAB = {"esophageal", "understanding", "onset", "tamponade",
             "deficiency", "values"}

    def test_joins_when_page_word(self):
        self.assertEqual(main._fix_bold_initials("- **E** sophageal rupture",
                                                  self.VOCAB),
                         "- **E**sophageal rupture")
        self.assertEqual(main._fix_bold_initials("**U** nderstanding the",
                                                  self.VOCAB),
                         "**U**nderstanding the")
        self.assertEqual(main._fix_bold_initials("**O** nset",
                                                  self.VOCAB),
                         "**O**nset")

    def test_leaves_legit_separate_letter(self):
        # "vitamin D deficiency": "Ddeficiency" NON è una parola di pagina
        self.assertEqual(main._fix_bold_initials("vitamin **D** deficiency",
                                                  self.VOCAB),
                         "vitamin **D** deficiency")

    def test_leaves_unknown_word(self):
        self.assertEqual(main._fix_bold_initials("**X** yz", self.VOCAB),
                         "**X** yz")

    def test_noop_without_vocab(self):
        self.assertEqual(main._fix_bold_initials("**E** sophageal", set()),
                         "**E** sophageal")

    def test_does_not_touch_long_bold(self):
        # solo l'iniziale singola: "**Note** something" resta invariato
        self.assertEqual(main._fix_bold_initials("**Note** something",
                                                  self.VOCAB),
                         "**Note** something")


if __name__ == "__main__":
    unittest.main()
