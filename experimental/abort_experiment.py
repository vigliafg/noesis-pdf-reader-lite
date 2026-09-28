#!/usr/bin/env python3
"""Abort dell'esperimento "motori di estrazione alternativi".

Riporta il repository ``noesis-pdf-reader-lite`` allo stato pulito del branch
base (default ``main``), annullando in un colpo solo tutto il piano
``PIANO-motori-estrazione-vlm.md``:

1. torna al branch base;
2. elimina il branch sperimentale ``experimental``;
3. rimuove i file non tracciati introdotti dall'esperimento (requirements dei
   motori, moduli nuovi, documenti di lavoro...);
4. opzionalmente scarica i pacchetti dei motori dal venv;
5. opzionalmente elimina le cache dei modelli scaricati
   (``~/.cache/xberg``, ``~/.mineru``, cache HuggingFace).

Uso tipico::

    python experimental/abort_experiment.py --yes
    python experimental/abort_experiment.py --yes --purge-models --purge-venv
    python experimental/abort_experiment.py --dry-run      # mostra e basta
    python experimental/abort_experiment.py --keep-plan    # conserva il piano

Exit code 0 = comando completato; il repository viene verificato e lo script
esce con 0 solo se ``git status`` è pulito (a meno di ``--dry-run``).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# --- Costanti dell'esperimento -------------------------------------------------

DEFAULT_BRANCH = "experimental"
DEFAULT_BASE = "main"
PLAN_DOC = "PIANO-motori-estrazione-vlm.md"

#: File/requirements che il piano puo' aggiungere (documentazione + rollback).
EXPERIMENT_REQUIREMENTS = (
    "requirements-xberg.txt",
    "requirements-marker.txt",
    "requirements-mineru.txt",
)

#: Pacchetti dei motori alternativi da disinstallare con ``--purge-venv``.
ENGINE_PACKAGES = (
    "xberg",
    "mineru",
    "mineru-llama-cpp",
    "marker-pdf",
    "surya-ocr",
    "pdftext",
    "docling",
)

#: Pattern HuggingFace creati dai motori (sottocartelle di models--<org>--<repo>).
HF_OWNERS = ("xberg-io", "opendatalab", "datalab-to")


# --- Helper --------------------------------------------------------------------


def echo(msg: str = "") -> None:
    print(msg, flush=True)


def run(
    cmd: list[str],
    *,
    cwd: Path,
    dry_run: bool = False,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Esegue un comando mostrandolo; in ``--dry-run`` si limita a stamparlo."""
    printable = " ".join(cmd)
    if dry_run:
        echo(f"  [dry-run] {printable}")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=capture,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"comando fallito ({result.returncode}): {printable}\n{detail}")
    return result


def git(
    args: list[str],
    *,
    cwd: Path,
    dry_run: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=cwd, dry_run=dry_run, check=check)


def find_repo_root(start: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(start),
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"non sono in un repository git: {start}")
    return Path(result.stdout.strip()).resolve()


def current_branch(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(root),
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def branch_exists(root: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=str(root),
    )
    return result.returncode == 0


def remote_branch_exists(root: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
        cwd=str(root),
    )
    return result.returncode == 0


def is_dirty(root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(root),
        text=True,
        capture_output=True,
    )
    return bool(result.stdout.strip())


def confirm(prompt: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        echo(f"{prompt} [auto: si]")
        return True
    reply = input(f"{prompt} [s/N] ").strip().lower()
    return reply in {"s", "si", "sì", "y", "yes"}


# --- Passi del rollback --------------------------------------------------------


def purge_model_caches(*, dry_run: bool) -> None:
    echo("\n== Cache modelli ==")
    targets: list[Path] = [
        Path.home() / ".cache" / "xberg",
        Path.home() / ".mineru",
    ]

    hf_hub = Path.home() / ".cache" / "huggingface" / "hub"
    if hf_hub.is_dir():
        for owner in HF_OWNERS:
            targets.extend(sorted(hf_hub.glob(f"models--{owner}--*")))

    if not targets:
        echo("  nessuna cache modelli trovata")
        return

    removed = 0
    for path in targets:
        if not path.exists():
            continue
        removed += 1
        echo(f"  rimuovo {path}")
        if not dry_run:
            shutil.rmtree(path, ignore_errors=True)

    if removed == 0:
        echo("  nessuna cache modelli presente")


def find_venv_python(root: Path) -> Path | None:
    candidates = (
        root / ".venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def purge_venv_packages(root: Path, *, dry_run: bool) -> None:
    echo("\n== Pacchetti del venv ==")
    python = find_venv_python(root)
    if python is None:
        echo("  venv non trovato (.venv/), salto")
        return

    to_uninstall = list(ENGINE_PACKAGES)
    for req in EXPERIMENT_REQUIREMENTS:
        req_path = root / req
        if req_path.is_file():
            to_uninstall.append(f"-r{req}")

    cmd = [str(python), "-m", "pip", "uninstall", "-y", *to_uninstall]
    # Non far fallire il rollback se un pacchetto non e' installato.
    run(cmd, cwd=root, dry_run=dry_run, check=False)


def clean_untracked(root: Path, *, keep_plan: bool, dry_run: bool) -> None:
    echo("\n== File non tracciati ==")
    cmd = ["clean", "-fd"]
    if keep_plan:
        cmd += ["-e", PLAN_DOC]
    # ``-fd`` (senza ``-x``): NON tocca i file ignorati come .venv/.
    git(cmd, cwd=root, dry_run=dry_run)


# --- Programma -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="abort_experiment.py",
        description="Annulla l'esperimento dei motori alternativi e riporta il "
        "repository allo stato pulito del branch base.",
    )
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="branch sperimentale (default: experimental)")
    parser.add_argument("--base", default=DEFAULT_BASE, help="branch di ritorno (default: main)")
    parser.add_argument("-y", "--yes", action="store_true", help="non chiedere conferma")
    parser.add_argument("--keep-branch", action="store_true", help="non eliminare il branch sperimentale")
    parser.add_argument("--keep-untracked", action="store_true", help="non eseguire 'git clean -fd'")
    parser.add_argument("--keep-plan", action="store_true", help=f"conserva {PLAN_DOC}")
    parser.add_argument("--purge-models", action="store_true", help="elimina le cache dei modelli scaricati")
    parser.add_argument("--purge-venv", action="store_true", help="disinstalla i motori alternativi dal venv")
    parser.add_argument("--delete-remote", action="store_true", help="elimina anche origin/<branch>")
    parser.add_argument("--dry-run", action="store_true", help="mostra i comandi senza eseguirli")
    parser.add_argument("--repo-root", default=None, help="radice del repository (default: autodetect)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dry_run = args.dry_run

    try:
        root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root(Path.cwd())
    except RuntimeError as exc:
        echo(f"ERRORE: {exc}")
        return 1

    echo(f"Repository : {root}")
    echo(f"Branch base: {args.base}")
    echo(f"Branch exp.: {args.branch}")
    if dry_run:
        echo("Modalita'    : DRY-RUN (nessuna modifica)")

    if not branch_exists(root, args.branch) and not args.keep_branch:
        echo(f"\nNessun branch locale '{args.branch}' da eliminare.")

    if is_dirty(root) and not args.yes:
        echo("\nIl working tree contiene modifiche non committate.")
        if not confirm("Procedo comunque (checkout forzato e clean)?", assume_yes=False):
            echo("Annullato.")
            return 1

    # Con `--keep-plan` il piano va messo al riparo prima del checkout: sul
    # branch sperimentale e' tracciato, quindi `git checkout main` lo rimuove.
    plan_backup: bytes | None = None
    plan_path = root / PLAN_DOC
    if args.keep_plan and not dry_run and plan_path.is_file():
        plan_backup = plan_path.read_bytes()

    # 1) Torna al branch base (forzato: l'esperimento va abbandonato).
    echo(f"\n== Passo a '{args.base}' ==")
    git(["checkout", "-f", args.base], cwd=root, dry_run=dry_run)

    # 2) Elimina il branch sperimentale.
    if not args.keep_branch:
        echo(f"\n== Elimino il branch '{args.branch}' ==")
        if branch_exists(root, args.branch) or dry_run:
            git(["branch", "-D", args.branch], cwd=root, dry_run=dry_run, check=not dry_run)
    else:
        echo(f"\n--keep-branch: conservo '{args.branch}'")

    # 3) Rimuove i file non tracciati dell'esperimento.
    if not args.keep_untracked:
        clean_untracked(root, keep_plan=args.keep_plan, dry_run=dry_run)

    # 4) Opzionale: cache modelli.
    if args.purge_models:
        purge_model_caches(dry_run=dry_run)

    # 5) Opzionale: pacchetti del venv.
    if args.purge_venv:
        purge_venv_packages(root, dry_run=dry_run)

    # 6) Opzionale: branch remoto.
    if args.delete_remote and remote_branch_exists(root, args.branch):
        echo(f"\n== Elimino origin/{args.branch} ==")
        git(["push", "origin", "--delete", args.branch], cwd=root, dry_run=dry_run, check=False)

    # 7) Opzionale: ripristina il piano come file non tracciato su base.
    if plan_backup is not None:
        echo(f"\n== Ripristino {PLAN_DOC} ==")
        plan_path.write_bytes(plan_backup)

    # Verifica finale.
    echo("\n== Stato finale ==")
    if dry_run:
        echo("dry-run completato: nessuna modifica applicata")
        return 0

    status = git(["status", "--porcelain"], cwd=root, check=False)
    dirty_lines = [
        line
        for line in (status.stdout or "").splitlines()
        if line.strip() and not (args.keep_plan and line.strip().endswith(PLAN_DOC))
    ]
    echo("\n".join(dirty_lines) if dirty_lines else "(vuoto)")
    if dirty_lines:
        echo("\nATTENZIONE: il repository non e' pulito (vedi sopra).")
        return 1
    if args.keep_plan:
        echo(f"\nRepository pulito. (conservato {PLAN_DOC}, non tracciato)")
    else:
        echo("\nRepository pulito: esperimento annullato.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        echo(f"\nERRORE: {exc}")
        sys.exit(1)
