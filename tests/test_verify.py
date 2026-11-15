import shlex
import sys

import pytest

from mender.diagnose import Diagnosis
from mender.render import JobResult, render_comment
from mender.verify import run_patched

CHECK = f"{shlex.quote(sys.executable)} -c 'import a; assert a.x == 2'"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / ".git").mkdir()  # must not be copied
    return tmp_path


def test_passes_with_a_correct_patch_and_leaves_the_checkout_alone(repo):
    res = run_patched(repo, {"a.py": "x = 2\n"}, CHECK)
    assert res.passed and res.returncode == 0
    assert (repo / "a.py").read_text() == "x = 1\n"


def test_fails_with_a_wrong_patch(repo):
    res = run_patched(repo, {"a.py": "x = 3\n"}, CHECK)
    assert not res.passed and "AssertionError" in res.output_tail


def test_rejects_paths_outside_the_repo(repo):
    with pytest.raises(ValueError, match="escapes"):
        run_patched(repo, {"../evil.py": "x = 2\n"}, CHECK)


def _result(status):
    d = Diagnosis(category="test_failure", summary="s", root_cause="r", evidence=[],
                  culprit_files=[], caused_by_pr=True, fix="", patch="--- a/a\n+++ b/a\n",
                  confidence="high")
    return JobResult("t", "", None, d, used_llm=True, patch_status=status,
                     verify_cmd="pytest -q")


def test_comment_states():
    verified = render_comment([_result("verified")], "a" * 40, "")
    assert "<code>pytest -q</code> passes" in verified and "```diff" in verified
    failing = render_comment([_result("tests_fail")], "a" * 40, "")
    assert "still failed" in failing and "```diff" not in failing
