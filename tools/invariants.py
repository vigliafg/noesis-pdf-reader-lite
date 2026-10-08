#!/usr/bin/env python3
"""Invarianti **strutturali** del markdown, indipendenti dal motore (Fase 0).

Il metro storico (``_reference_units``) e il motore condividono lo stesso modello
di layout: quando sbaglia il modello, sbagliano insieme (cecità correlata, es.
``su19 p383``). Qui si verificano **proprietà generali** del markdown rispetto
alla geometria di pagina, senza enumerare combinazioni di elementi:

- **I1 — contiguità dei box a tutta larghezza**: gli elementi dentro un
  rettangolo pieno full-width devono precedere il corpo sotto e seguire quello
  sopra (il box è una banda, non va interlacciato).
- **I3 — monotonia di banda**: se un blocco è interamente sotto un altro e si
  sovrappone in x, deve venire dopo nell'md.

Le posizioni si ancorano alle prime parole normalizzate dei blocchi (come
``verify_pages._order_report``). Un blocco senza ancora univoca viene saltato.
"""

from __future__ import annotations

_TEXY = {"text", "section-header", "title", "list-item"}
_BLOCKS = _TEXY | {"picture", "caption", "table"}


def _anchor(text: str, n: int = 5) -> str:
    import verify_pages as vp

    words = vp._norm(vp._strip_images(text or "")).split()
    return " ".join(words[:n])


def _area(b: tuple) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _overlap(a: tuple, b: tuple) -> bool:
    return (min(a[2], b[2]) - max(a[0], b[0]) > 0
            and min(a[3], b[3]) - max(a[1], b[1]) > 0)


def _in_box(e: dict, box: tuple, pad: float = 2.0) -> bool:
    b = e.get("bbox")
    if not b:
        return False
    x0, y0, x1, y1 = box
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    if x0 - pad <= cx <= x1 + pad and y0 - pad <= cy <= y1 + pad:
        return True
    ox = min(b[2], x1) - max(b[0], x0)
    oy = min(b[3], y1) - max(b[1], y0)
    if ox > 0 and oy > 0 and _area(b) > 0 and (ox * oy) / _area(b) >= 0.5:
        return True
    return False


def _positions(md_norm: str, elements: list[dict],
               classes: set[str]) -> list[tuple]:
    """``[(element, posizione_nell_md), ...]`` per i blocchi con ancora trovata."""
    out: list[tuple] = []
    for e in elements:
        if e.get("class") not in classes:
            continue
        a = _anchor(e.get("text") or "")
        if len(a) < 10:
            continue
        p = md_norm.find(" " + a + " ")
        if p >= 0:
            out.append((e, p))
    return out


def check_structure(md: str, elements: list[dict], boxes: list[tuple],
                    page_width: float) -> dict:
    """Ritorna ``{"ok": bool, "violations": [...]}`` per gli invarianti I1/I3."""
    import verify_pages as vp

    M = " " + vp._norm(vp._strip_images(md)) + " "
    viol: list[dict] = []

    # ── I1: contiguità dei box a tutta larghezza ─────────────────────────────
    # Si confronta il box coi soli blocchi **immediatamente adiacenti** (sopra e
    # sotto): usare *tutti* i blocchi sopra/sotto è fragile perché box vicini
    # (es. due tabelle impilate) condividono testo/abbreviazioni → ancore
    # ambigue → falsi positivi (`co23 p88`).
    for box in boxes or []:
        members = [e for e in elements
                   if e.get("class") in _BLOCKS and _in_box(e, box)]
        mp = _positions(M, members, _BLOCKS)
        if len(mp) < 2:
            continue
        pos = [p for _, p in mp]
        others = [e for e in elements
                  if e.get("class") in _BLOCKS and not _in_box(e, box)]
        above = [e for e in others if e["bbox"][3] <= box[1] - 2]
        below = [e for e in others if e["bbox"][1] >= box[3] + 2]
        prev = max(above, key=lambda e: e["bbox"][3], default=None)
        nxt = min(below, key=lambda e: e["bbox"][1], default=None)
        prev_p = _positions(M, [prev], _BLOCKS) if prev is not None else []
        next_p = _positions(M, [nxt], _BLOCKS) if nxt is not None else []
        bad = (bool(prev_p) and prev_p[0][1] > min(pos)) or (
            bool(next_p) and next_p[0][1] < max(pos))
        if bad:
            viol.append({
                "invariant": "I1", "kind": "box_non_contiguo",
                "box": [round(v, 1) for v in box], "n_members": len(mp),
                "note": "il box a tutta larghezza è interlacciato col corpo",
            })

    # ── I3: monotonia di banda ───────────────────────────────────────────────
    P = _positions(M, elements, _BLOCKS)
    inv = 0
    for i in range(len(P)):
        for j in range(len(P)):
            if i == j:
                continue
            a, pa = P[i]
            b, pb = P[j]
            if (a["bbox"][3] <= b["bbox"][1] and _overlap(a["bbox"], b["bbox"])
                    and pa > pb):
                inv += 1
    if inv:
        viol.append({
            "invariant": "I3", "kind": "banda_non_monotona", "inversions": inv,
            "note": "blocco sotto un altro (stessa x) emesso prima",
        })

    return {"ok": not viol, "violations": viol}
