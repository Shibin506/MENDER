"""Offline evaluation of the extraction + classification stages (and optionally the model).

    python eval/run_eval.py            # heuristics only, free, deterministic
    python eval/run_eval.py --llm      # also calls Claude for every case (costs money)

Metrics
  category accuracy     heuristic class == ground truth
  evidence kept         the must-see line survived extraction
  culprit in refs@3     ground-truth file is in the top 3 extracted file refs,
                        over cases where that file is named in the log at all
  kept / raw            share of the log characters sent onward
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.cases import CASES  # noqa: E402
from eval.holdout import HOLDOUT  # noqa: E402
from mender.classify import classify  # noqa: E402
from mender.logparse import extract  # noqa: E402

PY_PKGS = ["fastapi", "pydantic", "sqlalchemy", "httpx", "anyio", "starlette", "idna", "certifi",
           "urllib3", "charset-normalizer", "pytest", "pluggy", "iniconfig", "packaging",
           "typing-extensions", "sniffio", "h11", "click", "rich", "pygments", "markdown-it-py"]
NPM_WARN = ["npm warn deprecated inflight@1.0.6: This module is not supported, and leaks memory.",
            "npm warn deprecated glob@7.2.3: Glob versions prior to v9 are no longer supported",
            "npm warn deprecated rimraf@3.0.2: Rimraf versions prior to v4 are no longer supported"]
CRATES = ["serde", "serde_json", "itoa", "ryu", "memchr", "proc-macro2", "quote", "syn",
          "unicode-ident", "thiserror", "anyhow", "once_cell", "libc", "cfg-if"]
DISTRACTORS = [
    "tests/test_errors.py::test_error_message_is_readable PASSED",
    "  ✓ renders error boundary fallback (12 ms)",
    "warning: unused variable: `err`",
    "--- PASS: TestErrorWrapping (0.00s)",
    "INFO  retrying request after error (attempt 1/3) ... ok",
    "npm warn ERESOLVE overriding peer dependency",
    "Error handling middleware registered",
]


def noise(rng: random.Random, n: int) -> list[str]:
    out = ["##[group]Run actions/checkout@v4", "Syncing repository: acme/app",
           "Getting Git version info", "##[endgroup]",
           "##[group]Run actions/setup-python@v5", "Successfully set up CPython (3.12.3)",
           "##[endgroup]", "Cache restored from key: setup-python-Linux-x64-3.12-pip-4f1c"]
    while len(out) < n:
        kind = rng.random()
        if kind < 0.35:
            p = rng.choice(PY_PKGS)
            v = f"{rng.randint(0, 9)}.{rng.randint(0, 30)}.{rng.randint(0, 9)}"
            out += [f"Collecting {p}=={v}",
                    f"  Downloading {p}-{v}-py3-none-any.whl ({rng.randint(10, 900)} kB)"]
        elif kind < 0.5:
            c = rng.choice(CRATES)
            out.append(f"   Compiling {c} v1.{rng.randint(0, 40)}.{rng.randint(0, 9)}")
        elif kind < 0.6:
            out.append(rng.choice(NPM_WARN))
        elif kind < 0.68:
            out.append(rng.choice(DISTRACTORS))
        elif kind < 0.9:
            mod = rng.choice(["api", "cart", "users", "auth", "search", "billing"])
            out.append(f"tests/test_{mod}.py::test_case_{rng.randint(1, 400)} PASSED"
                       f"{' ' * 20}[{rng.randint(1, 99):>3}%]")
        else:
            out.append(f"added {rng.randint(100, 1500)} packages, and audited "
                       f"{rng.randint(100, 1600)} packages in {rng.randint(5, 60)}s")
    return out


def build_log(case: dict, seed: int) -> str:
    rng = random.Random(f"{case['id']}-{seed}")
    lines = noise(rng, rng.randint(1500, 4000))
    lines += case["failure"].splitlines()
    lines += ["##[error]Process completed with exit code 1.", "Post job cleanup.",
              "Cleaning up orphan processes"]
    t = 1714600000.0
    stamped = []
    for line in lines:
        t += rng.random() * 0.05
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t % 1 * 1e7):07d}Z"
        stamped.append(f"{ts} {line}")
    return "\n".join(stamped)


def run_heuristics(cases: list[dict], seed: int, verbose: bool) -> dict:
    rows = []
    for case in cases:
        raw = build_log(case, seed)
        ex = extract(raw)
        c = classify(ex)
        refs = [r.path for r in ex.file_refs[:3]]
        culprit = case["culprit"]
        culprit_in_log = bool(culprit) and culprit in case["failure"]
        rows.append({
            "id": case["id"],
            "expected": case["category"],
            "predicted": c.category.value,
            "category_ok": c.category.value == case["category"],
            # The pipeline skips the model when it is confident a failure is a flake.
            "skipped_llm": c.likely_flaky and c.confidence >= 0.8,
            "truly_flaky": case["category"] in ("infra_flake", "timeout"),
            "evidence_kept": case["must_see"] in ex.text,
            "culprit_in_log": culprit_in_log,
            "culprit_hit": culprit_in_log and culprit in refs,
            "raw_chars": len(raw),
            "kept_chars": len(ex.text),
            "raw_lines": len(raw.splitlines()),
            "refs": refs,
        })
    n = len(rows)
    with_culprit = [r for r in rows if r["culprit_in_log"]]
    summary = {
        "cases": n,
        "category_accuracy": sum(r["category_ok"] for r in rows) / n,
        "evidence_kept": sum(r["evidence_kept"] for r in rows) / n,
        "culprit_recall_at_3": (sum(r["culprit_hit"] for r in with_culprit) / len(with_culprit)
                                if with_culprit else None),
        "culprit_cases": len(with_culprit),
        "llm_skips": sum(r["skipped_llm"] for r in rows),
        "wrong_llm_skips": sum(r["skipped_llm"] and not r["truly_flaky"] for r in rows),
        "median_raw_lines": statistics.median(r["raw_lines"] for r in rows),
        "median_kept_ratio": statistics.median(r["kept_chars"] / r["raw_chars"] for r in rows),
        "token_reduction": 1 - sum(r["kept_chars"] for r in rows)
        / sum(r["raw_chars"] for r in rows),
    }
    print(f"{'case':28} {'expected':13} {'predicted':13} cat  evid  culprit  kept")
    for r in rows:
        culprit = ("hit" if r["culprit_hit"] else "MISS") if r["culprit_in_log"] else "-"
        print(f"{r['id']:28} {r['expected']:13} {r['predicted']:13} "
              f"{'ok ' if r['category_ok'] else 'X  '}  {'ok ' if r['evidence_kept'] else 'X  '}  "
              f"{culprit:7}  {r['kept_chars'] / r['raw_chars']:.1%}")
        if verbose and (not r["category_ok"] or not r["evidence_kept"]):
            print("    refs:", r["refs"])
    print()
    for k, v in summary.items():
        print(f"  {k:22} {v:.3f}" if isinstance(v, float) else f"  {k:22} {v}")
    return {"summary": summary, "rows": rows}


def run_llm(cases: list[dict], seed: int) -> dict:
    from mender.classify import classify as _classify
    from mender.config import get_settings
    from mender.diagnose import DiagnosisInput, TransientLLMError, build_diagnoser
    from mender.patch import dry_run

    settings = get_settings()
    diagnoser = build_diagnoser(settings)
    rows, errors = [], []
    for case in cases:
        ex = extract(build_log(case, seed))
        inp = DiagnosisInput(repo="acme/app", pr_number=1, pr_title="eval", job_name="ci",
                             failed_step=None, classification=_classify(ex),
                             log_excerpt=ex.text, files=case["files"])
        for attempt in range(5):
            try:
                d, usage = diagnoser.diagnose(inp)
                break
            except TransientLLMError as e:  # free tiers rate-limit hard; back off and retry
                print(f"  {case['id']}: {str(e)[:80]} - retry in {20 * (attempt + 1)}s")
                time.sleep(20 * (attempt + 1))
            except Exception as e:  # noqa: BLE001 - record and carry on with the next case
                d = None
                errors.append({"id": case["id"], "error": f"{type(e).__name__}: {e}"[:300]})
                break
        else:
            d = None
            errors.append({"id": case["id"], "error": "rate limited after 5 attempts"})
        if d is None:
            print(f"{case['id']:28} ERROR {errors[-1]['error'][:90]}")
            continue
        culprits = [f.path for f in d.culprit_files]
        patch = None
        if d.patch.strip():
            patch = dry_run(d.patch, lambda p, files=case["files"]: files.get(p)).ok
        rows.append({
            "id": case["id"], "category_ok": d.category == case["category"],
            "predicted": d.category,
            "culprit_ok": case["culprit"] in culprits if case["culprit"] else None,
            "patch_applies": patch, "cost_usd": usage.cost_usd, "latency_ms": usage.latency_ms,
            "model": usage.model, "summary": d.summary,
        })
        print(f"{case['id']:28} cat={'ok' if rows[-1]['category_ok'] else 'X ' + d.category} "
              f"culprit={rows[-1]['culprit_ok']} patch={patch} {usage.latency_ms}ms  "
              f"{d.summary[:70]}")
    patched = [r for r in rows if r["patch_applies"] is not None]
    culprit_rows = [r for r in rows if r["culprit_ok"] is not None]
    summary = {
        "category_accuracy": sum(r["category_ok"] for r in rows) / len(rows),
        "answered": len(rows),
        "errors": len(errors),
        "culprit_accuracy": sum(r["culprit_ok"] for r in culprit_rows) / len(culprit_rows),
        "patch_apply_rate": (sum(r["patch_applies"] for r in patched) / len(patched)
                             if patched else None),
        "patches_drafted": len(patched),
        "mean_cost_usd": statistics.mean(r["cost_usd"] or 0 for r in rows),
        "p50_latency_ms": statistics.median(r["latency_ms"] for r in rows),
    }
    print(json.dumps(summary, indent=2))
    return {"summary": summary, "rows": rows, "errors": errors}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--set", choices=["tune", "holdout", "all"], default="tune",
                    help="tune: cases the heuristics were developed on; holdout: unseen cases")
    ap.add_argument("--llm", action="store_true", help="also run Claude on every case")
    ap.add_argument("--min-accuracy", type=float, default=0.0,
                    help="exit non-zero if heuristic category accuracy is below this")
    ap.add_argument("--out", help="write results JSON here")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    cases = {"tune": CASES, "holdout": HOLDOUT, "all": CASES + HOLDOUT}[args.set]
    result = {"set": args.set, "heuristics": run_heuristics(cases, args.seed, args.verbose)}
    if args.llm:
        result["llm"] = run_llm(cases, args.seed)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, indent=2, default=str))
    acc = result["heuristics"]["summary"]["category_accuracy"]
    return 0 if acc >= args.min_accuracy else 1


if __name__ == "__main__":
    sys.exit(main())
