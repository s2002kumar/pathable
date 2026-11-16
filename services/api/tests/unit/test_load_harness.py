"""The capacity harness checks the answers the smoke test checks, and nothing else.

infra/vps/load/ drives load at the public pilot. Nothing at runtime would notice
if its expected journey drifted from smoke.py's (a wrong answer under load would
pass as right), if it called an endpoint the API no longer serves, or if its
synthetic client addresses left the documentation ranges and could collide with
a real visitor's rate-limit bucket. Its summariser reads server-side quantiles
from the API's histograms, and the latency-alert recommendation rests on those
numbers, so it must read them the way Prometheus does.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import json
import math
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from pathable_api.core.middleware import route_templates
from pathable_api.main import create_app
from tests.conftest import build_settings

REPO = Path(__file__).resolve().parents[4]
VPS = REPO / "infra" / "vps"
LOAD = VPS / "load"
WORKLOAD = (LOAD / "load.js").read_text(encoding="utf-8")

DOCUMENTATION_RANGES = {
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
}


def _module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_load_journey_expects_exactly_what_the_smoke_test_expects() -> None:
    smoke = _module("pathable_vps_smoke_under_test", VPS / "smoke.py")
    journey = json.loads((LOAD / "journey.json").read_text(encoding="utf-8"))
    expected = journey["expected"]

    assert expected["dataset_id"] == smoke.EXPECTED_DATASET
    assert (
        expected["standard"]["distance_m"],
        expected["standard"]["stairway_count"],
    ) == smoke.EXPECTED_STANDARD
    assert (
        expected["accessible"]["distance_m"],
        expected["accessible"]["stairway_count"],
    ) == smoke.EXPECTED_ACCESSIBLE
    assert journey["search"] == {
        "start_query": smoke.JOURNEY_START_QUERY,
        "start_label": smoke.JOURNEY_START_LABEL,
        "end_query": smoke.JOURNEY_END_QUERY,
        "end_label": smoke.JOURNEY_END_LABEL,
    }


def test_the_workload_calls_only_endpoints_the_api_serves() -> None:
    called = set(re.findall(r"['\"](/api/v1/[a-z/]+)['\"]", WORKLOAD))
    served = {template.path for template in route_templates(create_app(build_settings()))}

    assert called == {"/api/v1/routes/compare", "/api/v1/geocode/search"}
    assert called <= served


def test_synthetic_clients_come_only_from_the_documentation_ranges() -> None:
    declared = re.search(r"DOCUMENTATION_PREFIXES = \[([^\]]*)\]", WORKLOAD)
    assert declared is not None
    prefixes = re.findall(r"['\"]([\d.]+\.)['\"]", declared.group(1))

    assert {ipaddress.ip_network(f"{prefix}0/24") for prefix in prefixes} == DOCUMENTATION_RANGES
    # Any address written anywhere else in the script is one of them too.
    for literal in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", WORKLOAD):
        address = ipaddress.ip_address(literal)
        assert any(address in network for network in DOCUMENTATION_RANGES), literal


@pytest.mark.parametrize(
    ("q", "cumulative", "expected"),
    [
        # Rank exactly on a bucket's upper bound.
        (0.5, {0.1: 50.0, 0.25: 90.0, 0.5: 100.0, math.inf: 100.0}, 0.1),
        # Linear inside the bucket the rank falls in: 0.25 + 0.25 * 5/10.
        (0.95, {0.1: 50.0, 0.25: 90.0, 0.5: 100.0, math.inf: 100.0}, 0.375),
        # The first bucket's lower bound is zero: 0.1 * 12.5/50.
        (0.25, {0.1: 50.0, math.inf: 50.0}, 0.025),
        # A rank in +Inf answers the highest finite bound, as Prometheus does.
        (0.95, {1.0: 80.0, 10.0: 90.0, math.inf: 100.0}, 10.0),
    ],
)
def test_server_quantiles_are_read_the_way_prometheus_reads_them(
    q: float, cumulative: dict[float, float], expected: float
) -> None:
    summarise = _module("pathable_load_summarise_under_test", LOAD / "summarise.py")
    assert summarise.histogram_quantile(q, cumulative) == pytest.approx(expected)


def test_no_observations_is_no_quantile() -> None:
    summarise = _module("pathable_load_summarise_under_test", LOAD / "summarise.py")
    assert summarise.histogram_quantile(0.95, {0.1: 0.0, math.inf: 0.0}) is None
