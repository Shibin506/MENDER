"""Turn a raw CI log into a small set of high-signal excerpts.

A failed GitHub Actions job log is often tens of thousands of lines, almost all
of it setup noise (dependency installs, cache restores, compiler progress).
Sending all of it to a model is slow, expensive, and makes the answer worse.

The extractor:
  1. normalises lines (drops timestamps, ANSI escapes and runner bookkeeping),
  2. scores every line against known failure signatures for common toolchains,
  3. grows a window around each anchor, merges overlapping windows, and
  4. keeps the highest-scoring windows that fit in a character budget,
     returned in log order so the model reads them as a story.

It also pulls out file:line references, which drive which source files are
fetched as extra context and which files the eval expects to be blamed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ?")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_NOISE = re.compile(
    r"^(?:##\[(?:group|endgroup|debug)\]|::(?:group|endgroup|debug)::|"
    r"\s*(?:Downloading|Collecting|Requirement already satisfied|Using cached|"
    r"Installing collected packages|Successfully installed|added \d+ packages|"
    r"Compiling \S+ v\d|Downloaded \S+ v\d|Fetching|Cache (?:restored|saved)|"
    r"Received \d+ of \d+|Post job cleanup|Cleaning up orphan processes))"
)


@dataclass(frozen=True)
class Signature:
    name: str
    pattern: re.Pattern[str]
    weight: int
    before: int = 3
    after: int = 12


def _sig(name: str, pattern: str, weight: int, before: int = 3, after: int = 12) -> Signature:
    return Signature(name, re.compile(pattern), weight, before, after)


# Ordered roughly from most to least specific. Weights express how strongly a
# line indicates the *cause* of the failure rather than a symptom of it.
SIGNATURES: tuple[Signature, ...] = (
    # Python
    _sig("py_traceback", r"Traceback \(most recent call last\)", 8, 1, 25),
    _sig("pytest_failed", r"^(?:FAILED|ERROR) \S+::\S+", 9, 0, 2),
    _sig("pytest_assert", r"^E\s{2,}\S", 9, 6, 6),
    _sig("pytest_section", r"^_{5,} \S.* _{5,}$", 5, 0, 30),
    _sig("py_error", r"^\w+(?:Error|Exception): ", 7, 4, 3),
    _sig("mypy", r"^\S+\.pyi?:\d+: error: ", 9, 0, 1),
    _sig("ruff", r"^\S+\.py:\d+:\d+: [A-Z]+\d+ ", 8, 0, 2),
    _sig("missing_module", r"(?:ModuleNotFoundError: No module named|Cannot find module '|"
         r"cannot find package \")", 10, 3, 3),
    _sig("pip_resolution", r"(?:ResolutionImpossible|Could not find a version that satisfies|"
         r"No matching distribution found)", 9, 4, 6),
    # JavaScript / TypeScript
    _sig("tsc", r"\S+\.tsx?[(:]\d+[,:]\d+\)?:? error TS\d+", 9, 0, 3),
    _sig("jest_fail", r"^\s*(?:●|FAIL) \S", 8, 0, 20),
    _sig("vitest_fail", r"^\s*(?:×|✗|❯) \S+.*(?:failed|\d+ms)", 6, 0, 10),
    _sig("expect", r"^\s*(?:Expected|Received)(?: value)?:", 7, 3, 3),
    _sig("eslint", r"^\s+\d+:\d+\s+error\s+", 8, 2, 1),
    _sig("npm_err", r"^npm (?:ERR!|error) (?:code|ERESOLVE|404|E\d{3})", 9, 2, 12),
    # Go
    _sig("go_fail", r"^--- FAIL: ", 9, 0, 8),
    _sig("go_test_loc", r"^\s+\S+_test\.go:\d+: ", 8, 1, 4),
    _sig("go_build", r"^\S+\.go:\d+:\d+: ", 8, 1, 3),
    _sig("go_panic", r"^panic: ", 9, 0, 15),
    # Rust
    _sig("rust_error", r"^error(?:\[E\d{4}\])?: ", 9, 0, 14),
    _sig("rust_test_panic", r"thread '.*' panicked at", 9, 1, 8),
    # JVM
    _sig("gradle", r"^e: .*\.(?:kt|java):|\[ERROR\] .*\.java:\[\d+", 8, 0, 4),
    _sig("junit", r"^\S+ > \S+ FAILED$", 8, 0, 6),
    # Infra and environment
    _sig("oom", r"(?:exit code 137|Killed\b|out of memory|JavaScript heap out of memory)", 8, 4, 2),
    _sig("timeout", r"(?:has exceeded the maximum execution time|The operation was canceled|"
         r"timed out after|context deadline exceeded)", 7, 4, 2),
    _sig("network", r"(?:ECONNRESET|ETIMEDOUT|EAI_AGAIN|Connection reset by peer|"
         r"Could not resolve host|TLS handshake timeout|502 Bad Gateway|503 Service Unavailable|"
         r"toomanyrequests|rate limit exceeded)", 7, 4, 3),
    _sig("disk", r"No space left on device", 8, 3, 2),
    _sig("secret", r"(?:Input required and not supplied|secret .* (?:not set|is empty)|"
         r"Bad credentials|401 Unauthorized)", 7, 3, 3),
    # Generic markers, kept low so they only win when nothing specific matched
    _sig("gha_error", r"^##\[error\]", 4, 6, 2),
    _sig("generic_error", r"\b(?:error|ERROR|Error):", 2, 2, 3),
    _sig("exit_code", r"Process completed with exit code [1-9]", 1, 8, 0),
)


# file.py:12 / file.ts(12,5) / File "x.py", line 12 / --> src/lib.rs:10:5
_FILE_REFS = (
    re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+)'),
    re.compile(r"--> (?P<path>[\w./-]+\.\w+):(?P<line>\d+)"),
    re.compile(r"(?P<path>[\w./-]+\.(?:tsx?|jsx?|mjs|cjs))\((?P<line>\d+),\d+\)"),
    re.compile(r"(?P<path>[\w./-]+\.(?:java|kt|scala)):\[(?P<line>\d+),\d+\]"),
    # ESLint and some linters print the file on its own line, then indented findings
    re.compile(r"^\s*(?P<path>(?:/home/runner/work|/github/workspace)/\S+\.\w+)\s*$"),
    re.compile(
        r"(?<![\w/.])(?P<path>[\w.-]*/?[\w./-]*\.(?:py|pyi|ts|tsx|js|jsx|mjs|go|rs|java|kt|rb|"
        r"c|cc|cpp|h|hpp|cs|swift|php|scala|ya?ml|toml|json))[:](?P<line>\d+)"
    ),
)
_VENDORED = re.compile(
    r"(?:site-packages|dist-packages|node_modules|/usr/lib|/usr/local/lib|\.cargo/registry|"
    r"/go/pkg/mod|\.rustup|<frozen|^/opt/hostedtoolcache|/runner/_work/_temp)"
)
_WORKSPACE_PREFIX = re.compile(r"^(?:/home/runner/work/[^/]+/[^/]+/|/github/workspace/|\./)")


@dataclass(frozen=True)
class FileRef:
    path: str
    line: int | None = None


@dataclass
class Excerpt:
    start: int  # index into cleaned lines, inclusive
    end: int  # exclusive
    score: int
    signatures: set[str] = field(default_factory=set)
    text: str = ""


@dataclass
class Extraction:
    excerpts: list[Excerpt]
    file_refs: list[FileRef]
    matched_signatures: dict[str, int]
    total_lines: int
    raw_chars: int

    @property
    def text(self) -> str:
        return "\n...\n".join(e.text for e in self.excerpts)

    @property
    def compression(self) -> float:
        """Fraction of the raw log that was kept."""
        return len(self.text) / self.raw_chars if self.raw_chars else 0.0


def clean_lines(raw: str) -> list[str]:
    lines: list[str] = []
    for line in raw.splitlines():
        line = _ANSI.sub("", _TIMESTAMP.sub("", line)).rstrip()
        if not line or _NOISE.match(line):
            continue
        lines.append(line)
    return lines


def _normalise_path(path: str) -> str | None:
    if _VENDORED.search(path):
        return None
    path = _WORKSPACE_PREFIX.sub("", path)
    if path.startswith("/") or "://" in path:
        return None
    return path


def find_file_refs(lines: list[str], limit: int = 12) -> list[FileRef]:
    """File references in order of first appearance, project files only."""
    seen: dict[str, FileRef] = {}
    for line in lines:
        for pattern in _FILE_REFS:
            for m in pattern.finditer(line):
                path = _normalise_path(m.group("path"))
                if not path or path in seen:
                    continue
                line_no = m.groupdict().get("line")
                seen[path] = FileRef(path, int(line_no) if line_no else None)
        if len(seen) >= limit:
            break
    return list(seen.values())


def extract(raw: str, budget_chars: int = 24_000) -> Extraction:
    lines = clean_lines(raw)
    n = len(lines)
    matched: dict[str, int] = {}
    windows: list[Excerpt] = []

    for i, line in enumerate(lines):
        best: Signature | None = None
        for sig in SIGNATURES:
            if sig.pattern.search(line) and (best is None or sig.weight > best.weight):
                best = sig
        if best is None:
            continue
        matched[best.name] = matched.get(best.name, 0) + 1
        windows.append(
            Excerpt(max(0, i - best.before), min(n, i + best.after + 1), best.weight, {best.name})
        )

    merged: list[Excerpt] = []
    for w in sorted(windows, key=lambda w: w.start):
        if merged and w.start <= merged[-1].end:
            last = merged[-1]
            last.end = max(last.end, w.end)
            last.score += w.score
            last.signatures |= w.signatures
        else:
            merged.append(w)

    # When specific signatures matched, the generic "exit code" tail is noise.
    specific = [m for m in merged if m.signatures - {"exit_code", "generic_error", "gha_error"}]
    candidates = specific or merged
    if not candidates and n:
        # Nothing recognisable: fall back to the tail, where failures usually are.
        candidates = [Excerpt(max(0, n - 60), n, 0, {"tail"})]

    # Rank by score density so one huge window can't crowd out several sharp ones.
    ranked = sorted(candidates, key=lambda w: w.score / (w.end - w.start + 5), reverse=True)
    kept: list[Excerpt] = []
    used = 0
    for w in ranked:
        text = "\n".join(lines[w.start : w.end])
        if used + len(text) > budget_chars:
            remaining = budget_chars - used
            if remaining < 400:
                continue
            text = text[:remaining] + "\n[excerpt truncated]"
        w.text = text
        kept.append(w)
        used += len(text)
        if used >= budget_chars:
            break
    kept.sort(key=lambda w: w.start)

    refs = find_file_refs([ln for w in kept for ln in lines[w.start : w.end]])
    return Extraction(kept, refs, matched, n, len(raw))
