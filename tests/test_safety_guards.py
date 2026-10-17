"""Safety guards: secret redaction, filenames, error mapping, SSRF check.

Covers backend/app/safety.py. No network: getaddrinfo is mocked.
"""
from __future__ import annotations


def test_redact_removes_keys_and_tokens():
    from app.safety import redact

    assert "AIza" not in redact("key=AIzaSyD-abc123XYZ_456")
    assert redact("api_key=secret123") == "[redacted]"
    assert redact("token: abc") == "[redacted]"
    assert redact("see ?key=SECRET&x=1") == "see [redacted]&x=1"
    # Benign text untouched
    assert redact("photosynthesis converts light") == "photosynthesis converts light"


def test_safe_filename_strips_traversal_and_length():
    from app.safety import safe_filename

    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("a,b,c.txt") == "a_b_c.txt"
    assert safe_filename("") == "upload"
    long = "a" * 300 + ".txt"
    out = safe_filename(long)
    assert len(out) <= 180
    assert out.endswith(".txt")


def test_safe_error_message_maps_provider_failures():
    from app.errors import AppError, GeminiUnavailable
    from app.safety import safe_error_message

    assert "rate-limited" in safe_error_message(Exception("429 quota exceeded")).lower()
    assert "temporarily unavailable" in safe_error_message(Exception("503 overloaded")).lower()
    assert "GEMINI_API_KEY" in safe_error_message(Exception("API key not valid"))
    # AppError / ValueError keep their message (redacted); unknown is generic
    assert safe_error_message(AppError("bad input")) == "bad input"
    assert safe_error_message(ValueError("bad value")) == "bad value"
    assert safe_error_message(RuntimeError("weird internal x")) == "Something went wrong on the server. Try again in a moment."
    # Secrets never leak through the mapper
    assert "AIza" not in safe_error_message(ValueError("failed AIzaSyD-abc123XYZ_456"))


def test_assert_public_http_url_rejects_private(monkeypatch):
    import socket

    from app.safety import assert_public_http_url

    # Non-http schemes rejected without DNS
    try:
        assert_public_http_url("ftp://example.com/x")
        raise AssertionError("should have raised")
    except ValueError:
        pass
    try:
        assert_public_http_url("http://localhost:8000/x")
        raise AssertionError("should have raised")
    except ValueError:
        pass

    # Public host passes when DNS returns a public IP
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))])
    assert assert_public_http_url("https://example.com/a").startswith("https://")

    # Private / loopback IPs rejected
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 80))])
    try:
        assert_public_http_url("http://example.com/")
        raise AssertionError("should have raised for loopback")
    except ValueError:
        pass
