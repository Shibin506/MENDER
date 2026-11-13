"""Cheap, deterministic failure classification.

This runs before any model call. Its main job is to recognise failures that
are not the PR author's fault (network blips, runner OOM, timeouts, rate
limits) so they can be handled without spending tokens, and to give the model
a starting hypothesis for everything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .logparse import SIGNATURES, Extraction


class Category(StrEnum):
    TEST_FAILURE = "test_failure"
    BUILD_ERROR = "build_error"
    LINT = "lint"
    DEPENDENCY = "dependency"
    INFRA_FLAKE = "infra_flake"
    TIMEOUT = "timeout"
    CONFIG = "config"
    UNKNOWN = "unknown"


GROUPS: dict[Category, frozenset[str]] = {
    Category.TEST_FAILURE: frozenset({
        "pytest_failed", "pytest_assert", "pytest_section", "jest_fail", "vitest_fail",
        "expect", "go_fail", "go_test_loc", "rust_test_panic", "junit",
    }),
    Category.BUILD_ERROR: frozenset({"tsc", "go_build", "rust_error", "gradle", "mypy"}),
    Category.LINT: frozenset({"ruff", "eslint"}),
    Category.DEPENDENCY: frozenset({"pip_resolution", "npm_err", "missing_module"}),
    Category.INFRA_FLAKE: frozenset({"network", "disk", "oom"}),
    Category.TIMEOUT: frozenset({"timeout"}),
    Category.CONFIG: frozenset({"secret"}),
}

_CODE_GROUPS = (Category.BUILD_ERROR, Category.TEST_FAILURE, Category.LINT, Category.DEPENDENCY)
_TRANSIENT = re.compile(
    r"ECONNRESET|ETIMEDOUT|EAI_AGAIN|Connection reset by peer|Could not resolve host|"
    r"TLS handshake timeout|502 Bad Gateway|503 Service Unavailable|toomanyrequests|"
    r"rate limit exceeded|No space left on device|exit code 137|lost communication with the server",
    re.IGNORECASE,
)
_PY_ERRORS_IN_TESTS = re.compile(r"(?:^|\s)(?:test_\w+|tests?/)", re.MULTILINE)


@dataclass(frozen=True)
class Classification:
    category: Category
    confidence: float
    reason: str

    @property
    def likely_flaky(self) -> bool:
        return self.category in (Category.INFRA_FLAKE, Category.TIMEOUT)


_WEIGHTS = {s.name: s.weight for s in SIGNATURES}


def _score(extraction: Extraction, category: Category) -> int:
    """Weighted hit count: one specific signature outweighs several generic ones."""
    return sum(
        n * _WEIGHTS.get(name, 1)
        for name, n in extraction.matched_signatures.items()
        if name in GROUPS[category]
    )


def classify(extraction: Extraction) -> Classification:
    scores = {c: _score(extraction, c) for c in GROUPS}
    code_score = sum(scores[c] for c in _CODE_GROUPS if c is not Category.DEPENDENCY)
    text = extraction.text

    transient = _TRANSIENT.search(text)
    if transient and code_score == 0:
        return Classification(
            Category.INFRA_FLAKE, 0.85, f"transient error signature: {transient.group(0)!r}"
        )
    if scores[Category.TIMEOUT] and code_score == 0:
        return Classification(Category.TIMEOUT, 0.8, "job or step exceeded its time limit")
    if scores[Category.CONFIG] and code_score == 0 and not scores[Category.DEPENDENCY]:
        return Classification(Category.CONFIG, 0.7, "missing or invalid secret / input")

    ranked = sorted(_CODE_GROUPS, key=lambda c: scores[c], reverse=True)
    top = ranked[0]
    if scores[top] == 0:
        # A bare Python exception is a test failure if it happened inside tests.
        if extraction.matched_signatures.get("py_error") or extraction.matched_signatures.get(
            "py_traceback"
        ):
            if _PY_ERRORS_IN_TESTS.search(text):
                return Classification(Category.TEST_FAILURE, 0.5, "exception raised in tests")
            return Classification(Category.BUILD_ERROR, 0.4, "uncaught exception outside tests")
        return Classification(Category.UNKNOWN, 0.2, "no known failure signature matched")

    runner_up = scores[ranked[1]]
    confidence = round(min(0.95, 0.55 + 0.4 * (1 - runner_up / scores[top])), 2)
    names = sorted(n for n in extraction.matched_signatures if n in GROUPS[top])
    return Classification(top, confidence, "matched " + ", ".join(names))
