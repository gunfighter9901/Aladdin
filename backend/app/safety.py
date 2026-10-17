"""Input guards and log redaction. Never log credentials."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

_KEY_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z\-_]{10,}"),
    re.compile(r"(?i)(api[_-]?key|authorization|bearer|token|secret)\s*[=:]\s*\S+"),
    re.compile(r"(?i)(opik|gemini|google)[_-]?[a-z0-9]*key\s*[=:]\s*\S+"),
    re.compile(r"(?i)([?&](?:key|api[_-]?key|token)=)[^&\s]+"),
)


def redact(text: str) -> str:
    cleaned = text or ""
    for pattern in _KEY_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    return cleaned


def safe_error_message(exc: BaseException, limit: int = 400) -> str:
    import logging

    message = redact(str(exc)).strip() or exc.__class__.__name__
    lowered = message.lower()
    if "429" in message or "resource_exhausted" in lowered or "quota" in lowered:
        return "The model service is rate-limited right now. Wait a moment and try again."
    if "503" in message or "unavailable" in lowered or "overloaded" in lowered:
        return "The model service is temporarily unavailable. Try again shortly."
    if "api key" in lowered or "permission_denied" in lowered or "unauthenticated" in lowered:
        return "The Gemini API key was rejected. Check GEMINI_API_KEY in backend/.env."
    # Unknown errors: log the redacted detail for operators, return a generic
    # message so file paths and internals never leak to the browser.
    # User-facing validation errors (ValueError and AppError-shaped) keep
    # their message; everything else is generic. AppError is detected by
    # shape to avoid a hard import cycle.
    logging.getLogger("educator").warning("Unhandled error mapped to generic message: %s", message[:500])
    if isinstance(exc, ValueError) or exc.__class__.__name__ in ("AppError", "GeminiUnavailable"):
        return message[:limit]
    return "Something went wrong on the server. Try again in a moment."


def safe_filename(name: str) -> str:
    base = (name or "upload").replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._\- ]+", "_", base).strip("._ ")
    if not cleaned:
        return "upload"
    # Preserve a short extension when truncation would otherwise drop it.
    if len(cleaned) > 180:
        stem, dot, ext = cleaned.rpartition(".")
        if dot and 1 <= len(ext) <= 8 and ext.replace(" ", "").isalnum():
            budget = 179 - len(ext)
            stem = stem[:budget].rstrip("._ ") or "upload"
            return f"{stem}.{ext}"
        return cleaned[:180].rstrip("._ ") or "upload"
    return cleaned


def assert_public_http_url(url: str) -> str:
    """Reject non-http schemes and hosts that resolve to private networks.

    DNS resolution is blocking, so callers on the async path should use
    ``await asyncio.to_thread(assert_public_http_url, url)``. The check is a
    best-effort guard for an internal tool, not a sandbox boundary: DNS can
    change between this check and the outbound request.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only public http and https URLs are allowed.")
    host = parsed.hostname
    if not host:
        raise ValueError("URL is missing a host.")
    lowered = host.lower().rstrip(".")
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".local"):
        raise ValueError("Local addresses are not allowed.")
    try:
        infos = socket.getaddrinfo(lowered, parsed.port or 80)
    except socket.gaierror as exc:
        raise ValueError("Could not resolve that host.") from exc
    if not infos:
        raise ValueError("Could not resolve that host.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("URL resolves to a non-public address.")
    return parsed.geturl()
