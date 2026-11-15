import pytest

from mender.patch import PatchError, apply_file, dry_run, parse

from .conftest import GOOD_PATCH, PRICING_PY


def test_applies_clean_patch():
    res = dry_run(GOOD_PATCH, {"src/shop/pricing.py": PRICING_PY}.get)
    assert res.ok, res.error
    assert "1 - (1 - discount) * (1 - cart.coupon)" in res.files["src/shop/pricing.py"]
    assert "discount = cart.coupon" not in res.files["src/shop/pricing.py"]


def test_tolerates_line_offset():
    shifted = "# header\n# more\n" + PRICING_PY
    assert dry_run(GOOD_PATCH, {"src/shop/pricing.py": shifted}.get).ok


def test_rejects_context_mismatch():
    changed = PRICING_PY.replace("    if cart.coupon:", "    if cart.coupon is not None:")
    res = dry_run(GOOD_PATCH, {"src/shop/pricing.py": changed}.get)
    assert not res.ok
    assert "does not match" in res.error


def test_rejects_missing_file():
    res = dry_run(GOOD_PATCH, {}.get)
    assert not res.ok and "not found" in res.error


def test_rejects_wrong_hunk_counts():
    bad = GOOD_PATCH.replace("@@ -5,4 +5,4 @@", "@@ -5,9 +5,9 @@")
    with pytest.raises(PatchError, match="wrong line counts"):
        parse(bad)


def test_new_file():
    diff = "--- /dev/null\n+++ b/NOTES.md\n@@ -0,0 +1,2 @@\n+hello\n+world\n"
    res = dry_run(diff, lambda p: None)
    assert res.ok and res.files["NOTES.md"] == "hello\nworld"


def test_multiple_hunks_apply_in_order():
    src = "\n".join(f"line {i}" for i in range(1, 21))
    diff = (
        "--- a/f.txt\n+++ b/f.txt\n"
        "@@ -2,1 +2,1 @@\n-line 2\n+LINE 2\n"
        "@@ -15,1 +15,2 @@\n-line 15\n+LINE 15\n+inserted\n"
    )
    out = apply_file(src, parse(diff)[0]).split("\n")
    assert out[1] == "LINE 2" and out[14] == "LINE 15" and out[15] == "inserted"
    assert len(out) == 21


def test_garbage_is_rejected():
    assert not dry_run("I would change the discount logic.", lambda p: "x").ok


def test_recount_accepts_wrong_header_counts_when_the_lines_match():
    bad = GOOD_PATCH.replace("@@ -5,4 +5,4 @@", "@@ -5,9 +5,8 @@")
    res = dry_run(bad, {"src/shop/pricing.py": PRICING_PY}.get)  # recount is the default
    assert res.ok, res.error
    assert "1 - (1 - discount) * (1 - cart.coupon)" in res.files["src/shop/pricing.py"]
    assert not dry_run(bad, {"src/shop/pricing.py": PRICING_PY}.get, recount=False).ok


def test_recount_still_requires_the_content_to_match():
    bad = GOOD_PATCH.replace("@@ -5,4 +5,4 @@", "@@ -5,9 +5,8 @@").replace(
        "     if cart.coupon:", "     if cart.coupon is not None:"
    )
    res = dry_run(bad, {"src/shop/pricing.py": PRICING_PY}.get)
    assert not res.ok and "does not match" in res.error


def test_recount_handles_several_hunks_and_trailing_blank_lines():
    src = "\n".join(f"line {i}" for i in range(1, 21))
    diff = (
        "--- a/f.txt\n+++ b/f.txt\n"
        "@@ -2,3 +2,3 @@\n-line 2\n+LINE 2\n"
        "@@ -15,7 +15,1 @@\n-line 15\n+LINE 15\n+inserted\n\n\n"
    )
    out = dry_run(diff, {"f.txt": src}.get).files["f.txt"].split("\n")
    assert out[1] == "LINE 2" and out[14] == "LINE 15" and out[15] == "inserted"
    assert len(out) == 21
