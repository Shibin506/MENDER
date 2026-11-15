"""Command line entry points.

    mender serve                      run the webhook API
    mender worker                     run a queue worker
    mender diagnose --log ci.log      diagnose a local log, no GitHub needed
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from .classify import classify
from .config import get_settings
from .diagnose import DiagnosisInput
from .logparse import extract
from .patch import dry_run
from .pipeline import changed_files, heuristic_diagnosis, truncate_diff
from .render import JobResult, render_comment


def _local_file(root: Path, path: str) -> str | None:
    p = (root / path).resolve()
    if not p.is_relative_to(root.resolve()) or not p.is_file():
        return None
    return p.read_text(errors="replace")


def cmd_diagnose(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.provider:
        settings = settings.model_copy(update={"provider": args.provider})
    raw = Path(args.log).read_text(errors="replace") if args.log != "-" else sys.stdin.read()
    root = Path(args.repo_root)
    diff = Path(args.diff).read_text() if args.diff else ""
    if not diff and args.base:
        diff = subprocess.run(["git", "-C", str(root), "diff", args.base], capture_output=True,
                              text=True, check=True).stdout
    diff, truncated = truncate_diff(diff, settings.diff_budget_chars)

    extraction = extract(raw, settings.log_budget_chars)
    c = classify(extraction)
    print(f"[mender] {extraction.total_lines} log lines -> {len(extraction.excerpts)} excerpts "
          f"({extraction.compression:.1%} of raw), heuristic: {c.category} ({c.reason})",
          file=sys.stderr)

    if args.no_llm or (c.likely_flaky and c.confidence >= 0.8):
        result = JobResult("local", "", None, heuristic_diagnosis(extraction, c), used_llm=False)
    else:
        from .diagnose import build_diagnoser

        pr_files = changed_files(diff)
        refs = [r.path for r in extraction.file_refs]
        ordered = [p for p in refs if p in pr_files] + [p for p in refs if p not in pr_files]
        files = {}
        for path in dict.fromkeys(ordered):
            content = _local_file(root, path)
            if content is not None and len(files) < settings.max_context_files:
                files[path] = content
        inp = DiagnosisInput(
            repo=root.resolve().name, pr_number=None, pr_title=args.title, job_name="local",
            failed_step=None, classification=c, log_excerpt=extraction.text, diff=diff,
            diff_truncated=truncated, files=files,
        )
        diagnosis, usage = build_diagnoser(settings).diagnose(inp)
        result = JobResult("local", "", None, diagnosis, used_llm=True, usage=usage)
        if diagnosis.patch.strip():
            applied = dry_run(diagnosis.patch, lambda p: _local_file(root, p))
            result.patch_status = "valid" if applied.ok else "invalid"
            result.patch_error = applied.error

    if args.patch_out:
        if result.patch_status == "valid":
            Path(args.patch_out).write_text(result.diagnosis.patch.rstrip("\n") + "\n")
            print(f"[mender] verified patch written to {args.patch_out}", file=sys.stderr)
        else:
            print(f"[mender] no verified patch ({result.patch_status}: {result.patch_error})",
                  file=sys.stderr)

    if args.json:
        out = result.diagnosis.model_dump()
        out["patch_status"] = result.patch_status
        out["patch_error"] = result.patch_error
        print(json.dumps(out, indent=2))
    else:
        print(render_comment([result], "local", ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(prog="mender")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="run the webhook API")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)

    worker = sub.add_parser("worker", help="run a queue worker")
    worker.add_argument("--no-llm", action="store_true", help="heuristics only")

    diag = sub.add_parser("diagnose", help="diagnose a local CI log")
    diag.add_argument("--log", required=True, help="path to the log file, or - for stdin")
    diag.add_argument("--repo-root", default=".", help="checkout used for file context")
    diag.add_argument("--diff", help="path to a unified diff of the change")
    diag.add_argument("--base", help="compute the diff with `git diff BASE` in --repo-root")
    diag.add_argument("--title", default="Local change", help="PR title to give the model")
    diag.add_argument("--no-llm", action="store_true", help="heuristics only, no API call")
    diag.add_argument("--provider", choices=["claude", "gemini"], help="override MENDER_PROVIDER")
    diag.add_argument("--json", action="store_true", help="print the diagnosis as JSON")
    diag.add_argument("--patch-out", help="write the patch here if it applies cleanly")

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("mender.app:app", host=args.host, port=args.port)
        return 0
    if args.cmd == "worker":
        from .worker import Worker, build_diagnoser

        settings = get_settings()
        Worker(settings, build_diagnoser(settings, not args.no_llm)).run_forever()
        return 0
    return cmd_diagnose(args)


if __name__ == "__main__":
    sys.exit(main())
