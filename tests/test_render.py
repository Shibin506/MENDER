from mender.diagnose import Diagnosis, FileLocation
from mender.render import JobResult, render_comment


def make(**kw):
    base = dict(category="test_failure", summary="s", root_cause="r", evidence=[],
                culprit_files=[], caused_by_pr=True, fix="", patch="", confidence="high")
    base.update(kw)
    return JobResult("test", "https://gh/job", "pytest", Diagnosis(**base), used_llm=True)


def test_mentions_and_comment_markers_are_neutralised():
    body = render_comment([make(summary="ping @octocat please", root_cause="<!-- hide -->")],
                          "a" * 40, "https://run")
    assert "@octocat" not in body and "@​octocat" in body
    assert "<!-- hide -->" not in body


def test_evidence_with_backticks_cannot_break_the_fence():
    body = render_comment([make(evidence=["```", "rm -rf /"])], "a" * 40, "https://run")
    assert "````\n```\nrm -rf /\n````" in body


def test_lists_culprits_and_skipped_jobs():
    r = make(culprit_files=[FileLocation(path="src/a.py", line=3)])
    body = render_comment([r], "a" * 40, "https://run", skipped_jobs=["lint"])
    assert "`src/a.py:3`" in body and "Also failed (not analysed): `lint`" in body
