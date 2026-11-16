"""Regression: a route comparison answered 502 through Caddy because the API closed
the connection Caddy was reusing.

On the pilot, 2026-11-15, under load: Caddy logged ``read tcp …->api:8000: read:
connection reset by peer`` and returned 502 to a POST /api/v1/routes/compare
after 112 ms. uvicorn closes an idle keep-alive connection after 5 s by default;
Caddy keeps idle upstream connections for two minutes and reuses them, and does
not retry a POST. Whichever side closes an idle connection must be the proxy, so
the API's keep-alive has to outlast Caddy's, by a margin wide enough for a
saturated event loop that starts its timer before a large answer has been sent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import pathable_api.__main__ as entry
from tests.conftest import build_settings

REPO = Path(__file__).resolve().parents[4]
CADDYFILE = REPO / "infra" / "vps" / "Caddyfile"

#: reverse_proxy's ``transport http { keepalive }`` when the Caddyfile sets none.
CADDY_DEFAULT_KEEPALIVE_SECONDS = 120.0

#: Longer than any event-loop stall measured on the pilot (a 25 s route answer at
#: 50 requests in flight; an 11 s single cross-region route).
REQUIRED_MARGIN_SECONDS = 30.0

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def go_duration_seconds(value: str) -> float:
    """A Caddyfile duration (Go syntax, e.g. ``2m``, ``1m30s``) in seconds."""
    parts = _DURATION_PART.findall(value)
    if not parts or "".join(n + u for n, u in parts) != value:
        msg = f"not a duration Caddy would accept: {value!r}"
        raise ValueError(msg)
    return sum(float(number) * _UNIT_SECONDS[unit] for number, unit in parts)


def block_after(text: str, opener: str) -> str:
    """The brace-delimited block that follows ``opener`` in a Caddyfile."""
    start = text.index(opener)
    depth = 0
    for index in range(text.index("{", start), len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    msg = f"unclosed block after {opener!r}"
    raise ValueError(msg)


def caddy_api_keepalive_seconds(caddyfile: str) -> float:
    """How long Caddy keeps an idle connection to the API for reuse."""
    block = block_after(caddyfile, "reverse_proxy api:8000")
    found = re.search(r"^\s*keepalive\s+(\S+)\s*$", block, re.MULTILINE)
    if found is None:
        return CADDY_DEFAULT_KEEPALIVE_SECONDS
    if found.group(1) == "off":
        return 0.0
    return go_duration_seconds(found.group(1))


def test_the_api_keeps_idle_connections_open_longer_than_caddy_reuses_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(entry, "get_settings", build_settings)
    api_keep_alive = entry.build_server().config.timeout_keep_alive
    caddy_keepalive = caddy_api_keepalive_seconds(CADDYFILE.read_text(encoding="utf-8"))

    assert api_keep_alive >= caddy_keepalive + REQUIRED_MARGIN_SECONDS, (
        f"the API closes idle connections after {api_keep_alive} s but Caddy reuses them "
        f"for up to {caddy_keepalive} s: a reused connection can be reset and the request "
        "answered 502"
    )


@pytest.mark.parametrize(
    ("caddyfile", "expected"),
    [
        ("reverse_proxy api:8000 {\n\ttransport http {\n\t\tdial_timeout 5s\n\t}\n}", 120.0),
        ("reverse_proxy api:8000 {\n\ttransport http {\n\t\tkeepalive 1m30s\n\t}\n}", 90.0),
        ("reverse_proxy api:8000 {\n\ttransport http {\n\t\tkeepalive off\n\t}\n}", 0.0),
        (
            "reverse_proxy web:3000 {\n\ttransport http {\n\t\tkeepalive 10m\n\t}\n}\n"
            "reverse_proxy api:8000 {\n\ttransport http {\n\t\tkeepalive 45s\n\t}\n}",
            45.0,
        ),
    ],
)
def test_caddy_keepalive_is_read_from_the_api_upstream_only(
    caddyfile: str, expected: float
) -> None:
    assert caddy_api_keepalive_seconds(caddyfile) == expected
