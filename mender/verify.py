"""Run the project's tests against a patched scratch copy of the checkout.

A patch that applies can still be wrong. When a test command is given, the
patched files are written into a temporary copy of the repository and the
command is run there. The user's working tree is never touched.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_SKIP = shutil.ignore_patterns(".git", ".venv", "venv", "node_modules", "__pycache__",
                               ".pytest_cache", ".mypy_cache", ".ruff_cache", "target", "dist")


@dataclass
class VerifyResult:
    passed: bool
    returncode: int
    output_tail: str


def run_patched(root: Path, patched_files: dict[str, str], command: str,
                timeout: int = 600) -> VerifyResult:
    with tempfile.TemporaryDirectory(prefix="mender-verify-") as tmp:
        scratch = Path(tmp) / "repo"
        shutil.copytree(root, scratch, ignore=_SKIP, symlinks=True)
        for rel, content in patched_files.items():
            dest = (scratch / rel).resolve()
            if not dest.is_relative_to(scratch.resolve()):
                raise ValueError(f"patch path escapes the repository: {rel}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content if content.endswith("\n") else content + "\n")
        try:
            proc = subprocess.run(shlex.split(command), cwd=scratch, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return VerifyResult(False, -1, f"timed out after {timeout}s")
        out = (proc.stdout + proc.stderr).strip().splitlines()
        return VerifyResult(proc.returncode == 0, proc.returncode, "\n".join(out[-15:]))
