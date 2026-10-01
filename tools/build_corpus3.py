#!/usr/bin/env python3
"""Build corpus3: 30 open-access scientific PDFs with varied layouts.

Sources: arXiv (13), PLOS (8), bioRxiv (4), Zenodo (5).
Output: ./corpus3 (stable, in-repo dir; *.pdf are gitignored).

Run:  .venv/bin/python tools/build_corpus3.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "corpus3")

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ---- candidate works (ids chosen for layout variety) -----------------------
ARXIV = [  # math/finance, physics, ML, robotics, bio, astro, proofs
    "2606.00624", "2609.23240", "2609.30742", "2609.33753", "2609.33797",
    "2609.34576", "2609.37195", "2609.37412", "2609.37623", "2609.38104",
    "2609.38133", "2609.38151", "2609.38179",
]
PLOS = [  # PLOS ONE + PLOS Global Public Health
    "10.1371/journal.pone.0125826",
    "10.1371/journal.pone.0129065",
    "10.1371/journal.pone.0228263",
    "10.1371/journal.pone.0237749",
    "10.1371/journal.pone.0248753",
    "10.1371/journal.pone.0256464",
    "10.1371/journal.pone.0059363",
    "10.1371/journal.pgph.0001186",
]
BIORXIV = [  # line-numbered preprint layout
    "10.1101/2022.07.29.501882",
    "10.1101/2022.10.19.512946",
    "10.1101/2023.11.28.569048",
    "10.1101/2024.01.31.578257",
]
ZENODO = [  # Cyrillic, code, questionnaires, reports
    "21451572", "21452412", "21644463", "23057768", "23058054",
]


def http(url: str, timeout: int = 90, accept: str | None = None) -> bytes:
    headers = {"User-Agent": UA}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def save_pdf(url: str, dest: str, accept: str | None = None) -> bool:
    try:
        data = http(url, accept=accept)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        print(f"    x {url} -> {e}")
        return False
    if not data[:5].startswith(b"%PDF"):
        print(f"    x {url} -> non-PDF ({data[:16]!r})")
        return False
    with open(dest, "wb") as f:
        f.write(data)
    return True


def n_pages(path: str) -> int:
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            return doc.page_count
    except Exception:
        try:
            import fitz

            with fitz.open(path) as doc:
                return doc.page_count
        except Exception:
            return 0


def slug(name: str) -> str:
    return "".join(c if (c.isalnum() or c in ".-_") else "_" for c in name)


def try_urls(urls, dest) -> bool:
    for u in urls:
        if callable(u):
            u = u()
        if not u:
            continue
        if save_pdf(u, dest, accept=u_special_accept(u)):
            return True
    return False


def u_special_accept(url: str):
    if "zenodo.org/api/records" in url:
        return "application/json"
    return None


def build_arxiv():
    ok = []
    for aid in ARXIV:
        dest = os.path.join(OUT, f"arxiv_{slug(aid)}.pdf")
        if os.path.exists(dest):
            ok.append(dest)
            continue
        print(f"[arxiv] {aid}")
        if try_urls([f"https://arxiv.org/pdf/{aid}"], dest) and n_pages(dest) > 0:
            ok.append(dest)
    return ok


def build_plos():
    ok = []
    for doi in PLOS:
        dest = os.path.join(OUT, f"plos_{slug(doi)}.pdf")
        if os.path.exists(dest):
            ok.append(dest)
            continue
        print(f"[plos ] {doi}")
        journal = "globalpublichealth" if ".pgph." in doi else "plosone"
        urls = [
            f"https://journals.plos.org/{journal}/article/file?id={doi}&type=printable",
            f"https://journals.plos.org/plosone/article/file?id={doi}&type=printable",
        ]
        if try_urls(urls, dest) and n_pages(dest) > 0:
            ok.append(dest)
    return ok


def build_biorxiv():
    ok = []
    for doi in BIORXIV:
        dest = os.path.join(OUT, f"biorxiv_{slug(doi)}.pdf")
        if os.path.exists(dest):
            ok.append(dest)
            continue
        print(f"[biorx] {doi}")
        urls = [f"https://www.biorxiv.org/content/{doi}v{v}.full.pdf" for v in (1, 2, 3)]
        if try_urls(urls, dest) and n_pages(dest) > 0:
            ok.append(dest)
    return ok


def zenodo_file_url(rec: str) -> str | None:
    try:
        meta = json.loads(http(f"https://zenodo.org/api/records/{rec}",
                               accept="application/json"))
    except Exception as e:
        print(f"    x zenodo api {rec} -> {e}")
        return None
    for f in meta.get("files", []):
        key = f.get("key", "")
        if key.lower().endswith(".pdf"):
            self_link = f.get("links", {}).get("self")
            if self_link:
                return self_link
    return None


def build_zenodo():
    ok = []
    for rec in ZENODO:
        dest = os.path.join(OUT, f"zenodo_{slug(rec)}.pdf")
        if os.path.exists(dest):
            ok.append(dest)
            continue
        print(f"[zenod] {rec}")
        url = zenodo_file_url(rec)
        if url and try_urls([url + ("&" if "?" in url else "?") + "download=1", url], dest) \
                and n_pages(dest) > 0:
            ok.append(dest)
    return ok


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    groups = {
        "arxiv": build_arxiv(),
        "plos": build_plos(),
        "biorxiv": build_biorxiv(),
        "zenodo": build_zenodo(),
    }
    print("\n===== RIEPILOGO =====")
    total = 0
    for name, files in groups.items():
        total += len(files)
        pages = sum(n_pages(f) for f in files)
        print(f"{name:8} {len(files):2} pdf  {pages:5} pagine")
        for f in sorted(files):
            print(f"          - {os.path.basename(f)} ({n_pages(f)}p)")
    print(f"TOTALE   {total} pdf   {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
