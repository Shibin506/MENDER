import hashlib
import hmac

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from mender.github import COMMENT_MARKER, GitHubAppAuth, GitHubClient, verify_signature

API = "https://api.github.com"


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return pem, key.public_key()


@pytest.fixture
def gh(keypair):
    auth = GitHubAppAuth("123", keypair[0], API, httpx.Client())
    return GitHubClient(auth, 42, "acme/shop")


def test_verify_signature():
    body = b'{"a":1}'
    sig = "sha256=" + hmac.new(b"k", body, hashlib.sha256).hexdigest()
    assert verify_signature("k", body, sig)
    assert not verify_signature("k", body + b" ", sig)
    assert not verify_signature("", body, sig)
    assert not verify_signature("k", body, None)


def test_app_jwt_is_valid_rs256(gh, keypair):
    claims = jwt.decode(gh.auth.app_jwt(), keypair[1], algorithms=["RS256"])
    assert claims["iss"] == "123" and claims["exp"] - claims["iat"] <= 600


@respx.mock
def test_installation_token_is_cached(gh):
    route = respx.post(f"{API}/app/installations/42/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": "t1", "expires_at": "2999-01-01T00:00:00Z"})
    )
    assert gh.auth.installation_token(42) == "t1"
    assert gh.auth.installation_token(42) == "t1"
    assert route.call_count == 1


@respx.mock
def test_failed_jobs_and_step(gh):
    respx.post(f"{API}/app/installations/42/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": "t", "expires_at": "2999-01-01T00:00:00Z"}))
    respx.get(f"{API}/repos/acme/shop/actions/runs/5/jobs").mock(return_value=httpx.Response(
        200, json={"jobs": [
            {"id": 1, "name": "lint", "conclusion": "success", "steps": []},
            {"id": 2, "name": "test", "conclusion": "failure", "html_url": "u", "steps": [
                {"name": "Checkout", "conclusion": "success"},
                {"name": "Run pytest", "conclusion": "failure"}]},
        ]}))
    jobs = gh.failed_jobs(5)
    assert [(j.id, j.failed_step) for j in jobs] == [(2, "Run pytest")]


@respx.mock
def test_upsert_edits_existing_bot_comment(gh):
    respx.post(f"{API}/app/installations/42/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": "t", "expires_at": "2999-01-01T00:00:00Z"}))
    respx.get(f"{API}/repos/acme/shop/issues/7/comments").mock(return_value=httpx.Response(
        200, json=[
            {"id": 1, "body": f"{COMMENT_MARKER} spoofed", "user": {"type": "User"}},
            {"id": 2, "body": f"{COMMENT_MARKER}\nold", "user": {"type": "Bot"}},
        ]))
    patch = respx.patch(f"{API}/repos/acme/shop/issues/comments/2").mock(
        return_value=httpx.Response(200, json={"id": 2}))
    assert gh.upsert_comment(7, "new") == 2
    assert patch.called and b"new" in patch.calls[0].request.content


@respx.mock
def test_upsert_creates_when_absent(gh):
    respx.post(f"{API}/app/installations/42/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": "t", "expires_at": "2999-01-01T00:00:00Z"}))
    respx.get(f"{API}/repos/acme/shop/issues/7/comments").mock(
        return_value=httpx.Response(200, json=[]))
    create = respx.post(f"{API}/repos/acme/shop/issues/7/comments").mock(
        return_value=httpx.Response(201, json={"id": 77}))
    assert gh.upsert_comment(7, "hello") == 77 and create.called


@respx.mock
def test_file_at_returns_none_on_404(gh):
    respx.post(f"{API}/app/installations/42/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": "t", "expires_at": "2999-01-01T00:00:00Z"}))
    respx.get(f"{API}/repos/acme/shop/contents/missing.py").mock(
        return_value=httpx.Response(404))
    assert gh.file_at("missing.py", "abc") is None
