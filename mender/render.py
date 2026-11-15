"""Render diagnoses as a single, sticky PR comment."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .diagnose import Diagnosis, Usage

MAX_COMMENT_CHARS = 60_000  # GitHub rejects bodies over 65,536 characters

LABELS = {
    "test_failure": "Test failure",
    "build_error": "Build / type error",
    "lint": "Lint failure",
    "dependency": "Dependency problem",
    "infra_flake": "Infrastructure flake",
    "timeout": "Timeout",
    "config": "CI configuration",
    "unknown": "Unclassified failure",
}


@dataclass
class JobResult:
    job_name: str
    job_url: str
    failed_step: str | None
    diagnosis: Diagnosis
    used_llm: bool
    patch_status: str = "none"  # none | valid | invalid | verified | tests_fail
    verify_cmd: str | None = None
    patch_error: str | None = None
    usage: Usage | None = None
    rerun_requested: bool = False


def _neutralise(text: str) -> str:
    """Untrusted text must not ping people or close our code fences."""
    text = re.sub(r"@(?=[A-Za-z0-9-])", "@​", text)
    return text.replace("<!--", "&lt;!--")


def _link(text: str, url: str) -> str:
    return f"[{text}]({url})" if url else text


def _fence(body: str, lang: str = "") -> str:
    ticks = "`" * max(3, max((len(m) for m in re.findall(r"`{3,}", body)), default=0) + 1)
    return f"{ticks}{lang}\n{body}\n{ticks}"


def render_job(r: JobResult, head_sha: str) -> str:
    d = r.diagnosis
    label = LABELS.get(d.category, d.category)
    step = f" › `{_neutralise(r.failed_step)}`" if r.failed_step else ""
    out = [
        f"### ❌ {_link(_neutralise(r.job_name), r.job_url)}{step}",
        f"**{label}** · {d.confidence} confidence"
        + ("" if d.caused_by_pr else " · *likely not caused by this PR*"),
        "",
        f"**{_neutralise(d.summary)}**",
        "",
        _neutralise(d.root_cause),
    ]
    if d.culprit_files:
        files = ", ".join(
            f"`{_neutralise(f.path)}{f':{f.line}' if f.line else ''}`" for f in d.culprit_files[:5]
        )
        out += ["", f"**Where:** {files}"]
    if d.fix:
        out += ["", "**Fix:**", _neutralise(d.fix)]
    if r.rerun_requested:
        out += ["", "🔁 Mender re-ran the failed jobs once."]
    if d.evidence:
        out += [
            "",
            "<details><summary>Evidence from the log</summary>",
            "",
            _fence("\n".join(d.evidence[:15])),
            "",
            "</details>",
        ]
    if r.patch_status in ("valid", "verified") and d.patch:
        check = (f" and <code>{_neutralise(r.verify_cmd)}</code> passes"
                 if r.patch_status == "verified" and r.verify_cmd else "")
        out += [
            "",
            f"<details><summary>Suggested patch (applies cleanly to <code>{head_sha[:7]}</code>"
            f"{check})</summary>",
            "",
            _fence(_neutralise(d.patch.rstrip("\n")), "diff"),
            "",
            "</details>",
        ]
    elif r.patch_status == "tests_fail":
        out += ["", "<sub>A patch was drafted and applied, but "
                f"<code>{_neutralise(r.verify_cmd or 'the tests')}</code> still failed with it, "
                "so it is not shown.</sub>"]
    elif r.patch_status == "invalid":
        out += ["", "<sub>A patch was drafted but did not apply to the head commit, "
                "so it is not shown.</sub>"]
    return "\n".join(out)


def render_comment(
    results: list[JobResult], head_sha: str, run_url: str, skipped_jobs: list[str] | None = None
) -> str:
    sections = [render_job(r, head_sha) for r in results]
    if skipped_jobs:
        sections.append("Also failed (not analysed): " + ", ".join(
            f"`{_neutralise(j)}`" for j in skipped_jobs))
    costs = [r.usage.cost_usd for r in results if r.usage and r.usage.cost_usd is not None]
    models = sorted({r.usage.model for r in results if r.usage and r.usage.model})
    footer = f"<sub>Mender · {_link('workflow run', run_url)} · commit `{head_sha[:7]}`"
    if models:
        footer += f" · {', '.join(models)}"
        if costs:  # unknown for models without a price table entry
            footer += f" · ${sum(costs):.3f}"
    footer += " · react 👍 / 👎 to rate this diagnosis</sub>"
    body = "## 🩺 Mender CI diagnosis\n\n" + "\n\n---\n\n".join(sections) + "\n\n" + footer
    if len(body) > MAX_COMMENT_CHARS:
        body = body[: MAX_COMMENT_CHARS - 40] + "\n\n*(comment truncated)*"
    return body
