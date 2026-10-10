#!/usr/bin/env python3
"""Harness unico di verifica dell'app e dell'intero workflow.

**Scopo**: dare all'agente (e alla CI) un **unico comando** che verifica tutta
l'app, dal motore all'interfaccia, senza corpus e senza rete, e che produce un
report leggibile + un esito binario (exit code).

Layer eseguiti (in ordine):

1. ``unit``      — suite ``unittest`` completa (``tests/``): motore, regressioni,
   UI, i18n, tema, **workflow E2E** (``tests/test_workflow_e2e.py``).
2. ``pipeline``  — pipeline di estrazione E2E (``tools/e2e.py --mode auto``) su
   un PDF **sintetico**: gate testo/figure/tabelle + ``report.jsonl``.
3. ``visual``    — arbitrato visivo (``tools/e2e.py --mode visual``) su un PDF
   sintetico: produce la checklist ``review/index.md`` (default in ``--full``).
4. ``corpus``    — smoke su pagine reali di ``corpus1`` (saltato se il corpus
   non è presente; attivabile con ``--corpus``).

Uso (dalla root del repo)::

    .venv/bin/python tools/harness.py                 # full (unit+pipeline+visual)
    .venv/bin/python tools/harness.py --quick         # solo unit (veloce)
    .venv/bin/python tools/harness.py --module tests.test_workflow_e2e
    .venv/bin/python tools/harness.py --list
    .venv/bin/python tools/harness.py --out /tmp/opencode/harness

Esito: 0 se tutti i layer passano, 1 altrimenti. Il report è scritto in
``<out>/harness_report.json`` e ``<out>/harness_report.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_DEFAULT_OUT = Path(tempfile.gettempdir()) / "opencode" / "harness"

_LAYERS = {
    "unit": "Suite unittest completa (tests/) — motore, UI, i18n, workflow E2E",
    "pipeline": "Pipeline E2E (tools/e2e.py --mode auto) su PDF sintetico",
    "visual": "Arbitrato visivo (tools/e2e.py --mode visual) su PDF sintetico",
    "corpus": "Smoke su corpus reale (saltato se corpus1 assente)",
}


@dataclass
class LayerResult:
    name: str
    status: str = "pending"          # pass | fail | skip | error
    duration_s: float = 0.0
    detail: str = ""
    command: str = ""
    output_tail: str = ""
    metrics: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("pass", "skip")


def _env() -> dict:
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    return env


def _run(cmd: list[str], timeout: float, cwd: Path = _ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd), env=_env(),
        capture_output=True, text=True, timeout=timeout,
    )


def _tail(text: str, n: int = 4000) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else "...\n" + text[-n:]


# ── layer: unit ──────────────────────────────────────────────────────────────


def run_unit(out_dir: Path, timeout: float, module: str | None) -> LayerResult:
    """Run the full unittest suite (or a single module)."""
    if module:
        cmd = [sys.executable, "-m", "unittest", module, "-v"]
        label = f"unittest {module}"
    else:
        cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
        label = "unittest discover -s tests"
    res = LayerResult(name="unit", command=" ".join(cmd))
    t0 = time.perf_counter()
    try:
        proc = _run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        res.status = "error"
        res.detail = f"timeout dopo {timeout:.0f}s"
        res.duration_s = time.perf_counter() - t0
        return res
    res.duration_s = time.perf_counter() - t0
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    res.output_tail = _tail(combined)
    # Parse "Ran N tests in ...s" + "OK (skipped=K)" / "FAILED (...)"
    import re

    ran = re.search(r"Ran (\d+) tests? in ([\d.]+)s", combined)
    if ran:
        res.metrics["tests"] = int(ran.group(1))
    skipped = re.search(r"skipped=(\d+)", combined)
    if skipped:
        res.metrics["skipped"] = int(skipped.group(1))
    failed = re.search(r"failures=(\d+)", combined)
    if failed:
        res.metrics["failures"] = int(failed.group(1))
    errors = re.search(r"errors=(\d+)", combined)
    if errors:
        res.metrics["errors"] = int(errors.group(1))
    if proc.returncode == 0 and "OK" in combined:
        res.status = "pass"
        res.detail = (
            f"{res.metrics.get('tests', '?')} test, "
            f"{res.metrics.get('skipped', 0)} skip"
        )
    else:
        res.status = "fail"
        res.detail = "suite non verde"
    return res


# ── layer: pipeline / visual (tools/e2e.py) ──────────────────────────────────


def _synthetic_pdf(path: Path) -> bool:
    """A one-page PDF with text, a figure and a native table."""
    try:
        import pymupdf
    except Exception:  # noqa: BLE001
        return False
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 40), "CHAPTER 1 Synthetic harness page", fontsize=10)
    p.insert_textbox(
        pymupdf.Rect(50, 90, 280, 300),
        "They increase with a reduction in obesity related risk factors and "
        "the detection of diabetes improves outcomes for patients.",
        fontsize=10,
    )
    p.insert_textbox(
        pymupdf.Rect(320, 90, 550, 300),
        "Right column opening sentence with several words that must stay "
        "separate and in the correct reading order.",
        fontsize=10,
    )
    tx, ty = 60, 360
    rows = [["Item", "Qty", "Price"], ["Widget", "12", "4.50"],
            ["Bolt", "120", "0.20"]]
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
            p.insert_text((tx + ci * 110 + 4, ty + ri * 22 + 15), cell,
                          fontsize=10)
        p.draw_line((tx, ty + (ri + 1) * 22), (tx + 3 * 110, ty + (ri + 1) * 22))
    for ci in range(4):
        p.draw_line((tx + ci * 110, ty), (tx + ci * 110, ty + len(rows) * 22))
    doc.save(str(path))
    doc.close()
    return True


def run_e2e_layer(
    name: str, mode: str, out_dir: Path, timeout: float,
) -> LayerResult:
    """Run ``tools/e2e.py`` in ``auto``/``visual`` mode on a synthetic PDF."""
    res = LayerResult(name=name)
    tmp = Path(tempfile.mkdtemp(prefix=f"harness_{name}_"))
    pdf = tmp / "synthetic.pdf"
    if not _synthetic_pdf(pdf):
        res.status = "skip"
        res.detail = "pymupdf non disponibile"
        return res
    e2e_out = out_dir / f"e2e_{mode}"
    cmd = [
        sys.executable, str(_TOOLS / "e2e.py"), str(pdf),
        "--pages", "0", "--out", str(e2e_out), "--mode", mode,
        "--pipelines", "ir,current",
    ]
    res.command = " ".join(cmd)
    t0 = time.perf_counter()
    try:
        proc = _run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        res.status = "error"
        res.detail = f"timeout dopo {timeout:.0f}s"
        res.duration_s = time.perf_counter() - t0
        return res
    res.duration_s = time.perf_counter() - t0
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    res.output_tail = _tail(combined)
    if proc.returncode != 0:
        res.status = "fail"
        res.detail = f"exit {proc.returncode}"
        return res
    if mode == "auto":
        report = e2e_out / "report.jsonl"
        summary = e2e_out / "summary.md"
        if not report.exists():
            res.status = "fail"
            res.detail = "report.jsonl mancante"
            return res
        recs = [
            json.loads(ln)
            for ln in report.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        pipelines = sorted({r.get("pipeline") for r in recs})
        res.metrics = {
            "records": len(recs),
            "pipelines": pipelines,
            "summary": summary.exists(),
        }
        if not recs:
            res.status = "fail"
            res.detail = "nessun record nel report"
            return res
        res.status = "pass"
        res.detail = f"{len(recs)} record, pipeline {','.join(pipelines)}"
        return res
    # visual
    index = e2e_out / "review" / "index.md"
    if not index.exists():
        res.status = "fail"
        res.detail = "review/index.md mancante"
        return res
    res.status = "pass"
    res.detail = "review/index.md prodotto"
    res.metrics = {"review_index": True}
    return res


def run_corpus_layer(out_dir: Path, timeout: float) -> LayerResult:
    """Optional smoke on real corpus1 pages (skipped when the corpus is absent)."""
    res = LayerResult(name="corpus")
    corpus = _ROOT / "corpus1"
    pdfs = sorted(corpus.glob("*.pdf")) if corpus.is_dir() else []
    if not pdfs:
        res.status = "skip"
        res.detail = "corpus1 assente"
        return res
    try:
        import pymupdf
    except Exception:  # noqa: BLE001
        res.status = "skip"
        res.detail = "pymupdf non disponibile"
        return res
    pdf = pdfs[0]
    with pymupdf.open(pdf) as doc:
        n = doc.page_count
    page = min(5, max(0, n - 1))
    e2e_out = out_dir / "e2e_corpus"
    cmd = [
        sys.executable, str(_TOOLS / "e2e.py"), str(pdf),
        "--pages", str(page), "--out", str(e2e_out), "--mode", "auto",
        "--pipelines", "ir",
    ]
    res.command = " ".join(cmd)
    t0 = time.perf_counter()
    try:
        proc = _run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        res.status = "error"
        res.detail = f"timeout dopo {timeout:.0f}s"
        res.duration_s = time.perf_counter() - t0
        return res
    res.duration_s = time.perf_counter() - t0
    res.output_tail = _tail((proc.stdout or "") + "\n" + (proc.stderr or ""))
    if proc.returncode == 0 and (e2e_out / "report.jsonl").exists():
        res.status = "pass"
        res.detail = f"{pdf.name} p{page + 1}"
    else:
        res.status = "fail"
        res.detail = f"exit {proc.returncode}"
    return res


# ── report ───────────────────────────────────────────────────────────────────


def write_report(results: list[LayerResult], out_dir: Path, args) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    overall = "pass" if all(r.ok for r in results) else "fail"
    payload = {
        "overall": overall,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "args": vars(args),
        "layers": [asdict(r) for r in results],
    }
    (out_dir / "harness_report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Harness — report",
        "",
        f"- **Esito**: `{overall.upper()}`",
        f"- **Generato**: {payload['generated']}",
        "",
        "| layer | stato | durata | dettaglio |",
        "|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.name} | {r.status} | {r.duration_s:.1f}s | {r.detail} |")
    lines.append("")
    for r in results:
        if r.status in ("fail", "error"):
            lines.append(f"## {r.name} — {r.status}")
            lines.append("")
            lines.append("```")
            lines.append(r.output_tail)
            lines.append("```")
            lines.append("")
    (out_dir / "harness_report.md").write_text(
        "\n".join(lines), encoding="utf-8")
    return out_dir / "harness_report.md"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Harness unico di verifica dell'app e del workflow.")
    ap.add_argument("--out", default=str(_DEFAULT_OUT),
                    help="cartella del report (default: %(default)s)")
    ap.add_argument("--quick", action="store_true",
                    help="solo layer unit (veloce)")
    ap.add_argument("--module", default=None,
                    help="esegui solo questo modulo unittest (es. tests.test_workflow_e2e)")
    ap.add_argument("--corpus", action="store_true",
                    help="includi anche lo smoke su corpus reale")
    ap.add_argument("--no-visual", action="store_true",
                    help="salta il layer di arbitrato visivo")
    ap.add_argument("--timeout", type=float, default=1800.0,
                    help="timeout per layer in secondi (default: %(default)s)")
    ap.add_argument("--list", action="store_true",
                    help="elenca i layer ed esce")
    args = ap.parse_args(argv)

    if args.list:
        print("Layer del harness:")
        for name, desc in _LAYERS.items():
            print(f"  - {name:9s} {desc}")
        return 0

    out_dir = Path(args.out).expanduser()
    results: list[LayerResult] = []

    print("═" * 68)
    print("HARNESS — verifica app + workflow")
    print("═" * 68)

    def _emit(r: LayerResult) -> None:
        icon = {"pass": "✔", "fail": "✘", "skip": "•", "error": "✘"}.get(
            r.status, "?")
        print(f"  [{icon}] {r.name:9s} {r.duration_s:6.1f}s  {r.detail}")

    print("\n▶ unit")
    unit = run_unit(out_dir, args.timeout, args.module)
    results.append(unit)
    _emit(unit)

    if not args.quick and args.module is None:
        print("\n▶ pipeline")
        pipe = run_e2e_layer("pipeline", "auto", out_dir, args.timeout)
        results.append(pipe)
        _emit(pipe)

        if not args.no_visual:
            print("\n▶ visual")
            vis = run_e2e_layer("visual", "visual", out_dir, args.timeout)
            results.append(vis)
            _emit(vis)

        if args.corpus:
            print("\n▶ corpus")
            cor = run_corpus_layer(out_dir, args.timeout)
            results.append(cor)
            _emit(cor)

    report = write_report(results, out_dir, args)
    overall = "pass" if all(r.ok for r in results) else "fail"
    print("\n" + "═" * 68)
    print(f"ESITO: {overall.upper()}  ({len(results)} layer)")
    print(f"report: {report}")
    print("═" * 68)
    return 0 if overall == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
