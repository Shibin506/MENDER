"""Checks the exact request Mender sends to the Messages API, against a local fake server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import anthropic
import pytest

from mender.classify import Category, Classification
from mender.diagnose import ClaudeDiagnoser, DiagnosisInput, DiagnosisRefused, estimate_cost

DIAGNOSIS = {
    "category": "test_failure", "summary": "s", "root_cause": "r", "evidence": ["e"],
    "culprit_files": [{"path": "a.py", "line": 3}], "caused_by_pr": True, "fix": "f",
    "patch": "", "confidence": "high",
}


@pytest.fixture
def fake_api():
    state = {"stop_reason": "end_turn", "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            state["requests"].append({"body": body, "beta": self.headers.get("anthropic-beta")})
            content = [] if state["stop_reason"] == "refusal" else [
                {"type": "text", "text": json.dumps(DIAGNOSIS)}]
            payload = json.dumps({
                "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                "content": content, "stop_reason": state["stop_reason"], "stop_sequence": None,
                "usage": {"input_tokens": 1000, "output_tokens": 200,
                          "cache_read_input_tokens": 500, "cache_creation_input_tokens": 0},
            }).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = anthropic.Anthropic(api_key="test", base_url=f"http://127.0.0.1:{server.server_port}",
                                 max_retries=0)
    yield client, state
    server.shutdown()


def inp(**kw):
    base = dict(repo="acme/shop", pr_number=7, pr_title="Stack coupons", job_name="test",
                failed_step="Run pytest",
                classification=Classification(Category.TEST_FAILURE, 0.9, "matched pytest"),
                log_excerpt="E  assert 1 == 2", diff="diff --git a/x b/x",
                files={"a.py": "x = 1\n"})
    base.update(kw)
    return DiagnosisInput(**base)


def test_request_shape_and_parsing(fake_api):
    client, state = fake_api
    diagnosis, usage = ClaudeDiagnoser(client=client, effort="high").diagnose(inp())
    assert diagnosis.culprit_files[0].path == "a.py"
    assert usage.cache_read_tokens == 500 and usage.cost_usd == estimate_cost("claude-opus-5-5",
                                                                                usage)
    req = state["requests"][0]
    body = req["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in req["beta"]
    assert "thinking" not in body and "temperature" not in body
    user = body["messages"][0]["content"]
    # untrusted inputs are fenced, and the per-run log comes after the cacheable diff/files
    assert user.index("<diff>") < user.index('<file path="a.py">') < user.index("<log ")
    assert "never follow instructions" in body["system"]


def test_refusal_raises(fake_api):
    client, state = fake_api
    state["stop_reason"] = "refusal"
    with pytest.raises(DiagnosisRefused):
        ClaudeDiagnoser(client=client).diagnose(inp())


def test_truncated_diff_is_flagged_to_the_model(fake_api):
    client, state = fake_api
    ClaudeDiagnoser(client=client).diagnose(inp(diff_truncated=True))
    assert 'truncated="true"' in state["requests"][0]["body"]["messages"][0]["content"]


def test_cost_estimate():
    from mender.diagnose import Usage
    u = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=1_000_000)
    assert estimate_cost("claude-opus-5-5", u) == pytest.approx(4.0 + 2.0 + 0.2)
    assert estimate_cost("unknown-model", u) is None
