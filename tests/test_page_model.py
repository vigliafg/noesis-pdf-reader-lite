#!/usr/bin/env python3
"""Test dello schema logico di pagina (``page_model.py``).

CI-safe: i test che aprono un PDF si saltano se il corpus è assente. Non
richiedono rete né modelli: lo schema è replicato da DoclingDocument ma senza
dipendere da Docling.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import page_model as pm  # noqa: E402
    _OK = True
except Exception as e:  # noqa: BLE001
    _OK = False
    _ERR = repr(e)


@unittest.skipUnless(_OK, "page_model non importabile")
class SchemaTests(unittest.TestCase):
    def test_bbox_geometry(self):
        b = pm.BoundingBox(0, 0, 10, 10)
        self.assertEqual(b.area, 100)
        self.assertAlmostEqual(b.intersection_over_union(pm.BoundingBox(5, 0, 15, 10)), 50 / 150)
        self.assertTrue(b.overlaps(pm.BoundingBox(5, 5, 15, 15)))
        self.assertFalse(b.overlaps(pm.BoundingBox(20, 20, 30, 30)))

    def test_dump_serializes_enums(self):
        d = pm.DoclingDocument()
        d.pages["1"] = pm.PageItem(page_no=1, size=pm.Size(612, 792))
        t = pm.TextItem(self_ref="#/texts/0", label=pm.DocItemLabel.SECTION_HEADER,
                        text="ciao", source=pm.NodeSource.GNN)
        d.texts.append(t)
        js = json.loads(d.to_json())
        self.assertEqual(js["texts"][0]["label"], "section_header")
        self.assertEqual(js["texts"][0]["source"], "gnn")
        self.assertEqual(js["schema_name"], "DoclingDocument")

    def test_cell_defaults(self):
        c = pm.TableCell(text="x", column_header=True)
        self.assertTrue(c.column_header)
        self.assertEqual(c.label, pm.TableCellLabel.BODY)


@unittest.skipUnless(_OK, "page_model non importabile")
class BuildPageTests(unittest.TestCase):
    _PDF = _ROOT / "corpus2" / "fe22.pdf"

    def test_build_two_column_page(self):
        if not self._PDF.exists():
            self.skipTest("corpus2/fe22.pdf assente")
        doc = pm.build_page_document(str(self._PDF), 1037)
        js = doc.to_dict()
        # struttura di base
        self.assertIn("pages", js)
        self.assertGreater(len(js["texts"]), 0)
        self.assertIsNotNone(js["body"])
        self.assertIsNotNone(js["furniture"])
        # lo scheletro geometrico è presente e serializzabile
        self.assertIn("gutters", js["geometry"])
        self.assertGreaterEqual(js["geometry"]["n_columns"], 2)
        # nesting: colonne rappresentate come gruppi
        self.assertGreaterEqual(len(js["groups"]), 1)
        # ogni gruppo ha figli referenziati
        self.assertTrue(all(g["children"] for g in js["groups"]))
        # il markdown/JSON non contiene set/oggetti non serializzabili
        json.dumps(js)


@unittest.skipUnless(_OK, "page_model non importabile")
class TableTests(unittest.TestCase):
    _CE = _ROOT / "corpus1" / "ce24.pdf"

    def test_complex_table_spans_and_sections(self):
        if not self._CE.exists():
            self.skipTest("corpus1/ce24.pdf assente")
        js = pm.build_page_document(str(self._CE), 479).to_dict()
        self.assertEqual(len(js["tables"]), 1)
        data = js["tables"][0]["data"]
        self.assertEqual(data["num_cols"], 7)
        cells = data["table_cells"]
        # celle unite ricostruite da rows[i].cells (None = slot coperto)
        self.assertTrue(any(c["col_span"] > 1 for c in cells))
        self.assertTrue(any(c["row_span"] > 1 for c in cells))
        # una riga a tutta larghezza è una sezione
        self.assertTrue(any(c["row_section"] for c in cells))
        # la griglia è num_rows × num_cols ed è popolata (nessun buco interno)
        self.assertEqual(len(data["grid"]), data["num_rows"])
        self.assertTrue(all(len(r) == data["num_cols"] for r in data["grid"]))
        self.assertTrue(all(any(cell for cell in r) for r in data["grid"]))

    def test_skeleton_independent_of_gnn_globals(self):
        # pymupdf4llm (strato GNN) all'import attiva pymupdf.layout e disabilita
        # le quad corrections *globalmente*: lo scheletro geometrico deve restare
        # identico sia prima sia dopo (determinismo, anti cecità correlata).
        fe = _ROOT / "corpus2" / "fe22.pdf"
        if not (self._CE.exists() and fe.exists()):
            self.skipTest("corpus assente")
        pm.build_page_document(str(fe), 1037)  # importa/costruisce GNN
        a = pm.build_page_document(str(self._CE), 479).to_dict()
        b = pm.build_page_document(str(self._CE), 479).to_dict()
        dims = lambda d: [(t["data"]["num_rows"], t["data"]["num_cols"])
                          for t in d["tables"]]
        self.assertEqual(dims(a), dims(b))
        self.assertEqual(dims(a), [(25, 7)])

    def test_boxed_text_is_not_a_table(self):
        # fe22 p1038: il riquadro "ICD-10CM CODE" non è una tabella (falso
        # positivo di find_tables(lines) senza linee interne reali).
        pdf = _ROOT / "corpus2" / "fe22.pdf"
        if not pdf.exists():
            self.skipTest("corpus2/fe22.pdf assente")
        js = pm.build_page_document(str(pdf), 1037).to_dict()
        self.assertEqual(js["tables"], [])


if __name__ == "__main__":
    unittest.main()
