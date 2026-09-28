"""Tests for the experimental-engines rollback (``experimental/abort_experiment.py``).

Verifies that the abort tool returns a repository to a clean ``main`` from an
``experimental`` branch, deleting the branch and the untracked files introduced
by the experiment, and that ``--dry-run`` changes nothing.

Run with (from the project root):

    .venv/bin/python -m unittest discover -s tests -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ABORT = _ROOT / "experimental" / "abort_experiment.py"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=True,
    )


@unittest.skipUnless(shutil.which("git"), "git non disponibile")
@unittest.skipUnless(_ABORT.is_file(), "abort_experiment.py non presente")
class AbortExperimentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="noesis-abort-")
        self.repo = Path(self._tmp.name)
        _git(self.repo, "init")
        _git(self.repo, "config", "user.email", "test@example.com")
        _git(self.repo, "config", "user.name", "Test")
        _git(self.repo, "config", "commit.gpgsign", "false")

        (self.repo / "app.txt").write_text("base\n", encoding="utf-8")
        _git(self.repo, "add", "app.txt")
        _git(self.repo, "commit", "-m", "base")
        _git(self.repo, "branch", "-M", "main")

        # Branch sperimentale con un file tracciato e uno non tracciato.
        _git(self.repo, "checkout", "-b", "experimental")
        (self.repo / "experimental").mkdir()
        (self.repo / "requirements-xberg.txt").write_text("xberg\n", encoding="utf-8")
        (self.repo / "PIANO-motori-estrazione-vlm.md").write_text("# piano\n", encoding="utf-8")
        _git(self.repo, "add", "requirements-xberg.txt", "PIANO-motori-estrazione-vlm.md")
        _git(self.repo, "commit", "-m", "experiment")
        (self.repo / "leftover.tmp").write_text("non tracciato\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(_ABORT), *args],
            cwd=str(self.repo),
            text=True,
            capture_output=True,
        )

    def test_abort_returns_to_clean_main(self) -> None:
        result = self._run("--yes")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

        branches = _git(self.repo, "branch").stdout
        self.assertNotIn("experimental", branches)
        self.assertIn("main", branches)

        status = _git(self.repo, "status", "--porcelain").stdout.strip()
        self.assertEqual(status, "", f"working tree sporco:\n{status}")

        self.assertFalse((self.repo / "requirements-xberg.txt").exists())
        self.assertFalse((self.repo / "leftover.tmp").exists())
        self.assertFalse((self.repo / "PIANO-motori-estrazione-vlm.md").exists())
        self.assertTrue((self.repo / "app.txt").exists())

    def test_keep_plan_preserves_document(self) -> None:
        result = self._run("--yes", "--keep-plan")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue((self.repo / "PIANO-motori-estrazione-vlm.md").exists())
        self.assertFalse((self.repo / "requirements-xberg.txt").exists())

    def test_dry_run_changes_nothing(self) -> None:
        result = self._run("--dry-run", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

        branches = _git(self.repo, "branch").stdout
        self.assertIn("experimental", branches)
        self.assertTrue((self.repo / "requirements-xberg.txt").exists())
        self.assertTrue((self.repo / "leftover.tmp").exists())


if __name__ == "__main__":
    unittest.main()
