"""Parse and dry-run unified diffs.

Models produce plausible-looking diffs whose context lines don't match the
real file. Posting those wastes reviewers' time, so every suggested patch is
applied in memory against the file contents at the PR's head commit. Only
patches that apply cleanly are shown as a suggested fix.

Matching is exact on content (no fuzz) but tolerant of line-number offsets,
the same trade-off `git apply` makes by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(ValueError):
    pass


@dataclass
class Hunk:
    old_start: int
    lines: list[str] = field(default_factory=list)  # each starts with ' ', '-' or '+'

    @property
    def old(self) -> list[str]:
        return [ln[1:] for ln in self.lines if ln[0] in " -"]

    @property
    def new(self) -> list[str]:
        return [ln[1:] for ln in self.lines if ln[0] in " +"]


@dataclass
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


def _strip_prefix(path: str) -> str | None:
    path = path.split("\t", 1)[0].strip()
    if path == "/dev/null":
        return None
    return path[2:] if path[:2] in ("a/", "b/") else path


def parse(diff: str) -> list[FilePatch]:
    patches: list[FilePatch] = []
    lines = diff.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            patches.append(FilePatch(_strip_prefix(line[4:]), _strip_prefix(lines[i + 1][4:])))
            i += 2
            continue
        m = _HUNK.match(line)
        if m:
            if not patches:
                raise PatchError("hunk before any file header")
            old_len = int(m.group(2) or 1)
            new_len = int(m.group(4) or 1)
            hunk = Hunk(int(m.group(1)))
            i += 1
            seen_old = seen_new = 0
            while i < len(lines) and (seen_old < old_len or seen_new < new_len):
                body = lines[i]
                if body.startswith("\\"):  # "\ No newline at end of file"
                    i += 1
                    continue
                if body == "":
                    body = " "  # some generators drop the space on blank context lines
                tag = body[0]
                if tag not in " -+":
                    break
                hunk.lines.append(body)
                seen_old += tag in " -"
                seen_new += tag in " +"
                i += 1
            if seen_old != old_len or seen_new != new_len:
                raise PatchError(
                    f"hunk @@ -{m.group(1)} in {patches[-1].path} has wrong line counts "
                    f"(header says -{old_len}/+{new_len}, body has -{seen_old}/+{seen_new})"
                )
            patches[-1].hunks.append(hunk)
            continue
        i += 1
    if not patches or not any(p.hunks for p in patches):
        raise PatchError("no file hunks found")
    return patches


def _find(haystack: list[str], needle: list[str], hint: int) -> int:
    """Position of `needle` in `haystack` closest to `hint`, or -1."""
    if not needle:
        return min(max(hint, 0), len(haystack))
    best = -1
    for pos in range(len(haystack) - len(needle) + 1):
        if haystack[pos : pos + len(needle)] == needle and (
            best < 0 or abs(pos - hint) < abs(best - hint)
        ):
            best = pos
    return best


def apply_file(original: str | None, fp: FilePatch) -> str:
    if fp.old_path is None:
        if original is not None:
            raise PatchError(f"{fp.path}: patch creates a file that already exists")
        src: list[str] = []
    else:
        if original is None:
            raise PatchError(f"{fp.path}: file not found at head commit")
        src = original.split("\n")

    out: list[str] = []
    cursor = 0
    offset = 0
    for hunk in fp.hunks:
        hint = hunk.old_start - 1 + offset
        pos = _find(src[cursor:], hunk.old, hint - cursor)
        if pos < 0:
            raise PatchError(f"{fp.path}: hunk at line {hunk.old_start} does not match the file")
        pos += cursor
        out.extend(src[cursor:pos])
        out.extend(hunk.new)
        cursor = pos + len(hunk.old)
        offset += len(hunk.new) - len(hunk.old)
    out.extend(src[cursor:])
    return "\n".join(out)


@dataclass
class ApplyResult:
    ok: bool
    files: dict[str, str]
    error: str | None = None


def dry_run(diff: str, read_file) -> ApplyResult:
    """Apply `diff` in memory. `read_file(path)` returns contents or None."""
    try:
        patches = parse(diff)
        files = {fp.path: apply_file(read_file(fp.old_path) if fp.old_path else None, fp)
                 for fp in patches}
    except PatchError as e:
        return ApplyResult(False, {}, str(e))
    return ApplyResult(True, files)
