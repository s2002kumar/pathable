"""The dashboard, alerts and agent config agree with the metrics the API exports.

They live in infra/vps/ and are read by Grafana Cloud and Alloy, so nothing at
runtime would notice a metric renamed here and not there: the panel would go
blank and the alert would never fire. These tests read those files the way
their consumers do and fail on the first name, route, job or port that the API
or the agent does not actually provide.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from pathable_api.core.metrics import Metrics
from pathable_api.core.middleware import route_templates
from pathable_api.main import create_app
from tests.conftest import build_settings

REPO = Path(__file__).resolve().parents[4]
GRAFANA = REPO / "infra" / "vps" / "grafana"
DASHBOARD = GRAFANA / "pathable-dashboard.json"
ALERTS = GRAFANA / "alerts.rules.yaml.template"
ALLOY = REPO / "infra" / "vps" / "alloy" / "config.alloy"
VPS_COMPOSE = REPO / "infra" / "vps" / "compose.yaml"

#: Series that come from somewhere other than the API's own collectors.
FROM_ALLOY = {"up", "probe_success", "probe_http_duration_seconds"}
FROM_PROCESS_COLLECTOR = {"process_resident_memory_bytes", "process_cpu_seconds_total"}

_SERIES = re.compile(r"\b(pathable_[a-z0-9_]+|process_[a-z_]+|probe_[a-z_]+|up)\b(?=\s*[{\[)])")
_ROUTE = re.compile(r'route="([^"]+)"')
_JOB = re.compile(r'job="([^"]+)"')
_PROBE = re.compile(r'probe="([^"]+)"')


def _dashboard() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    return loaded


def _dashboard_queries() -> list[str]:
    return [
        target["expr"] for panel in _dashboard()["panels"] for target in panel.get("targets", [])
    ]


def _queries() -> list[str]:
    return [*_dashboard_queries(), ALERTS.read_text(encoding="utf-8")]


def _exported_series() -> set[str]:
    """Every sample name the API's registry can produce."""
    names: set[str] = set()
    metrics = Metrics(version="test")
    for family in metrics.registry.collect():
        suffixes = {
            "counter": ("_total",),
            "histogram": ("_bucket", "_count", "_sum"),
        }.get(family.type, ("",))
        names.update(f"{family.name}{suffix}" for suffix in suffixes)
    return names


def test_the_dashboard_has_queries_to_check() -> None:
    # Guards the tests below against passing vacuously over an empty file.
    assert _dashboard()["uid"] == "pathable-pilot"
    assert len(_dashboard_queries()) >= 10


@pytest.mark.parametrize("query", _queries())
def test_every_series_queried_is_one_something_exports(query: str) -> None:
    exported = _exported_series() | FROM_ALLOY | FROM_PROCESS_COLLECTOR
    for name in _SERIES.findall(query):
        if name.startswith("process_"):
            assert name in FROM_PROCESS_COLLECTOR, f"{name} is not a process metric we chart"
        assert name in exported, f"{name} is queried but nothing exports it"


def test_every_route_queried_is_one_the_api_serves() -> None:
    templates = {template.path for template in route_templates(create_app(build_settings()))}
    routes = {route for query in _queries() for route in _ROUTE.findall(query)}

    assert routes, "the dashboard should break latency down by endpoint"
    assert routes <= templates


def test_every_job_and_probe_queried_is_one_alloy_produces() -> None:
    alloy = ALLOY.read_text(encoding="utf-8")
    jobs = {job for query in _queries() for job in _JOB.findall(query)}
    probes = {probe for query in _queries() for probe in _PROBE.findall(query)}

    assert jobs == {"pathable-api"}
    assert 'job_name        = "pathable-api"' in alloy
    assert probes == {"pathable-public-readiness"}
    assert '"probe" = "pathable-public-readiness"' in alloy


def test_alloy_scrapes_the_port_the_api_is_told_to_serve_on() -> None:
    compose = VPS_COMPOSE.read_text(encoding="utf-8")
    alloy = ALLOY.read_text(encoding="utf-8")

    port = re.search(r'METRICS_PORT: "(\d+)"', compose)
    assert port is not None
    assert f'"__address__" = "api:{port.group(1)}"' in alloy


def test_the_metrics_port_is_never_published() -> None:
    # Only Caddy publishes ports; the exposition has no authentication.
    compose = VPS_COMPOSE.read_text(encoding="utf-8")
    port = re.search(r'METRICS_PORT: "(\d+)"', compose)
    assert port is not None
    assert f"{port.group(1)}:" not in compose
    assert compose.count("ports:") == 1


def test_the_latency_threshold_is_left_to_be_decided() -> None:
    # The p95 threshold is set from observed production latency, never guessed
    # into the committed rules.
    assert "${ROUTE_P95_THRESHOLD_SECONDS}" in ALERTS.read_text(encoding="utf-8")


def test_the_series_pattern_finds_names_in_a_real_query() -> None:
    # A guard on the guard: if the pattern found nothing, every check above
    # would pass without looking at anything.
    query = 'sum(rate(pathable_http_requests_total{job="pathable-api"}[5m])) / up{job="x"}'
    assert _SERIES.findall(query) == ["pathable_http_requests_total", "up"]
