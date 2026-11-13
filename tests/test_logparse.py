from mender.classify import Category, classify
from mender.logparse import clean_lines, extract, find_file_refs

from .conftest import FLAKY_LOG, PYTEST_LOG


def test_clean_lines_strips_timestamps_ansi_and_noise():
    raw = (
        "2024-05-01T10:00:00.1234567Z \x1b[31mError: boom\x1b[0m\n"
        "2024-05-01T10:00:00.1234567Z ##[group]Run foo\n"
        "2024-05-01T10:00:00.1234567Z Collecting requests\n"
        "\n"
    )
    assert clean_lines(raw) == ["Error: boom"]


def test_extract_keeps_failure_and_drops_setup():
    ex = extract(PYTEST_LOG)
    assert "E       assert 90.0 == 81.0" in ex.text
    assert "FAILED tests/test_pricing.py::test_member_discount_stacks" in ex.text
    assert "Collecting fastapi" not in ex.text


def test_file_refs_are_repo_relative_and_skip_vendored_paths():
    refs = find_file_refs([
        '  File "/home/runner/work/app/app/app/main.py", line 10, in handler',
        '  File "/opt/hostedtoolcache/Python/3.12/lib/python3.12/site-packages/x.py", line 3',
        "src/api/client.ts(34,22): error TS2339: nope",
        "node_modules/react/index.js:12:3",
        "  --> src/parser.rs:47:20",
    ])
    assert [(r.path, r.line) for r in refs] == [
        ("app/main.py", 10), ("src/api/client.ts", 34), ("src/parser.rs", 47),
    ]


def test_extract_respects_budget():
    raw = "\n".join(f"FAILED tests/test_{i}.py::test_x - AssertionError: {'x' * 200}"
                    for i in range(500))
    ex = extract(raw, budget_chars=5000)
    assert len(ex.text) <= 5000 + 200


def test_extract_falls_back_to_tail_when_nothing_matches():
    raw = "\n".join(f"step {i} did a thing" for i in range(200))
    ex = extract(raw)
    assert "step 199 did a thing" in ex.text
    assert "step 0 did a thing" not in ex.text


def test_generic_exit_code_dropped_when_specific_match_exists():
    ex = extract(PYTEST_LOG)
    assert all(e.signatures != {"exit_code"} for e in ex.excerpts)


def test_classify_flake_vs_code():
    assert classify(extract(FLAKY_LOG)).category is Category.INFRA_FLAKE
    c = classify(extract(PYTEST_LOG))
    assert c.category is Category.TEST_FAILURE
    assert not c.likely_flaky


def test_network_noise_inside_a_real_test_failure_is_not_a_flake():
    log = PYTEST_LOG + "2024-05-01T10:00:05Z WARNING retry after ECONNRESET\n"
    assert classify(extract(log)).category is Category.TEST_FAILURE
