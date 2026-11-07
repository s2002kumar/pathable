"""Prometheus metrics: bounded, private, and never able to fail a request.

What is under test is what an operator's scrape would carry. Each test drives
the real application through its HTTP stack and reads the exposition the
metrics port would serve, so a label that leaked a coordinate or grew with
traffic fails here rather than in a hosted Prometheus.

Successful route comparisons need a routable graph, so their outcome and
timing are covered against real PostGIS in tests/integration/test_routing_api.py.
"""

from __future__ import annotations

import logging
import socket
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

import pathable_api.core.middleware as middleware_module
from pathable_api.core.metrics import (
    HTTP_METHODS,
    HTTP_STATUSES,
    OTHER,
    ROUTE_PROFILES,
    UNMATCHED_ROUTE,
    UNPARSED,
    Metrics,
    bounded,
    http_method,
    http_status,
    serve_metrics,
    status_class,
)
from pathable_api.core.middleware import route_template, route_templates
from pathable_api.geo.geocoding import GeocodeResult, GeocodingError, SearchOutcome
from pathable_api.geo.regions import RegionDefinition
from pathable_api.main import create_app
from tests.conftest import build_settings, settings_from
from tests.metrics_scrape import exposition, samples, value

COMPARE = "/api/v1/routes/compare"
SEARCH = "/api/v1/geocode/search"
LIVE = "/api/v1/health/live"
VERSION = "0.1.0+metricstest"

#: Values that would identify a person, a place or a request if they reached a
#: label. Each is distinctive enough that finding it in the exposition can only
#: mean it leaked.
SECRET_LATITUDE = 43.4712345678
SECRET_LONGITUDE = -80.5398765432
SECRET_QUERY = "SECRETQUERY31415 Erb Street West"
SECRET_FORWARDED_FOR = "203.0.113.77"
SECRET_REQUEST_ID = "req-SECRETID-271828"
SECRET_EXCEPTION = "SECRETEXCEPTION-161803 connection to db:5432 refused"


def route_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "region": "waterloo",
        "origin": {"latitude": SECRET_LATITUDE, "longitude": SECRET_LONGITUDE},
        "destination": {"latitude": 43.4729, "longitude": -80.5422},
        "profile": "wheelchair",
    }
    body.update(overrides)
    return body


@pytest.fixture
def app() -> FastAPI:
    return create_app(build_settings(app_version=VERSION, rate_limit_routes_per_minute=1000))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


class _Provider:
    """A geocoder that answers from a script: matches, nothing, or a failure."""

    name = "local"
    enabled = True
    autocomplete = True

    def __init__(self, answer: SearchOutcome | Exception) -> None:
        self._answer = answer

    async def search(
        self, query: str, *, region: RegionDefinition, limit: int = 5, autocomplete: bool = False
    ) -> SearchOutcome:
        del query, region, limit, autocomplete
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


FOUND = SearchOutcome(
    matches=[GeocodeResult(label="Davis Centre", longitude=-80.5422, latitude=43.4729)],
    attribution="© OpenStreetMap contributors, ODbL 1.0",
)


class TestLabelValues:
    def test_an_unknown_value_folds_into_one(self) -> None:
        assert bounded("wheelchair", ROUTE_PROFILES) == "wheelchair"
        assert bounded("../../etc/passwd", ROUTE_PROFILES) == OTHER

    def test_a_status_the_api_sends_stays_exact_and_any_other_is_its_class(self) -> None:
        assert http_status(422) == "422"
        assert http_status(429) == "429"
        assert http_status(418) == "4xx"
        assert http_status(599) == "5xx"
        assert http_status(999) == OTHER
        assert http_status(-1) == OTHER

    def test_status_classes(self) -> None:
        assert [status_class(code) for code in (101, 204, 308, 404, 503)] == [
            "1xx",
            "2xx",
            "3xx",
            "4xx",
            "5xx",
        ]

    def test_an_invented_method_is_other(self) -> None:
        assert http_method("post") == "POST"
        assert http_method("BREW") == OTHER


class TestRouteTemplates:
    def test_a_request_is_labelled_with_the_template_it_matched(self, app: FastAPI) -> None:
        assert route_template(route_templates(app), "POST", COMPARE) == COMPARE
        assert route_template(route_templates(app), "GET", LIVE) == LIVE

    def test_an_unknown_path_is_unmatched(self, app: FastAPI) -> None:
        assert route_template(route_templates(app), "GET", "/wp-login.php") == UNMATCHED_ROUTE

    def test_a_wrong_method_still_names_the_endpoint(self, app: FastAPI) -> None:
        assert route_template(route_templates(app), "GET", COMPARE) == COMPARE

    def test_every_documented_endpoint_has_a_template(self, app: FastAPI) -> None:
        # A router added later is covered without touching the middleware.
        templates = {template.path for template in route_templates(app)}
        assert set(app.openapi()["paths"]) <= templates
        assert "/openapi.json" in templates


class TestHttpMetrics:
    def test_a_request_is_counted_and_timed_by_template_and_status(
        self, app: FastAPI, client: TestClient
    ) -> None:
        client.get(LIVE)
        client.get(LIVE)

        assert (
            value(app, "pathable_http_requests_total", method="GET", route=LIVE, status="200") == 2
        )
        assert (
            value(
                app,
                "pathable_http_request_duration_seconds_count",
                method="GET",
                route=LIVE,
                status_class="2xx",
            )
            == 2
        )

    def test_a_wrong_method_is_a_405_on_its_endpoint(
        self, app: FastAPI, client: TestClient
    ) -> None:
        assert client.get(COMPARE).status_code == 405
        assert value(app, "pathable_http_requests_total", route=COMPARE, status="405") == 1

    def test_a_body_refused_as_too_large_is_counted_against_its_endpoint(
        self, app: FastAPI, client: TestClient
    ) -> None:
        response = client.post(
            COMPARE, content=b"{}", headers={"content-length": str(10 * 1024 * 1024)}
        )
        assert response.status_code == 413
        assert value(app, "pathable_http_requests_total", route=COMPARE, status="413") == 1

    def test_a_rate_limited_request_is_counted_against_its_endpoint(self) -> None:
        app = create_app(build_settings(rate_limit_routes_per_minute=1))
        with TestClient(app) as client:
            client.post(COMPARE, json=route_body())
            assert client.post(COMPARE, json=route_body()).status_code == 429

        assert value(app, "pathable_http_requests_total", route=COMPARE, status="429") == 1

    def test_an_unhandled_failure_is_a_counted_500_whose_message_goes_nowhere(
        self, app: FastAPI, client: TestClient
    ) -> None:
        app.state.geocoder = _Provider(RuntimeError(SECRET_EXCEPTION))

        response = client.post(SEARCH, json={"query": "anything"})

        assert response.status_code == 500
        assert value(app, "pathable_http_requests_total", route=SEARCH, status="500") == 1
        assert value(app, "pathable_place_searches_total", outcome="error") == 1
        assert "SECRETEXCEPTION" not in exposition(app)


class TestBoundedCardinality:
    def test_scanning_hundreds_of_paths_adds_one_series_not_hundreds(
        self, app: FastAPI, client: TestClient
    ) -> None:
        client.get(f"/{uuid.uuid4()}")
        before = exposition(app).count("\n")

        scanned = [f"/api/v1/{uuid.uuid4()}/admin?token={uuid.uuid4()}" for _ in range(200)]
        for path in scanned:
            client.get(path)

        text = exposition(app)
        assert text.count("\n") == before
        assert (
            value(
                app,
                "pathable_http_requests_total",
                method="GET",
                route=UNMATCHED_ROUTE,
                status="404",
            )
            == 201
        )
        assert not any(path.split("/")[3] in text for path in scanned)

    def test_an_invented_method_does_not_become_a_label(
        self, app: FastAPI, client: TestClient
    ) -> None:
        client.request("BREW", LIVE)
        assert "BREW" not in exposition(app)


class TestRouteOutcomes:
    def test_a_server_that_cannot_route_counts_an_error(
        self, app: FastAPI, client: TestClient
    ) -> None:
        # No database in this application, so routing is unavailable.
        assert client.post(COMPARE, json=route_body()).status_code == 503

        assert (
            value(app, "pathable_route_comparisons_total", profile="wheelchair", outcome="error")
            == 1
        )
        # Nothing was computed, so there is no comparison time to report.
        assert samples(app, "pathable_route_comparison_duration_seconds_count") == []

    def test_a_body_that_never_parsed_counts_as_invalid_without_a_profile(
        self, app: FastAPI, client: TestClient
    ) -> None:
        response = client.post(COMPARE, json=route_body(profile="jetpack"))

        assert response.status_code == 422
        assert (
            value(app, "pathable_route_comparisons_total", profile=UNPARSED, outcome="invalid") == 1
        )
        assert "jetpack" not in exposition(app)


class TestPlaceSearchOutcomes:
    @pytest.mark.parametrize(
        ("answer", "outcome"),
        [
            (FOUND, "success"),
            (SearchOutcome(matches=[]), "empty"),
            (SearchOutcome(searched=False), "not_searched"),
            (GeocodingError("Place search is unavailable right now."), "error"),
        ],
    )
    def test_each_answer_has_its_own_outcome(
        self, app: FastAPI, client: TestClient, answer: SearchOutcome | Exception, outcome: str
    ) -> None:
        app.state.geocoder = _Provider(answer)

        client.post(SEARCH, json={"query": "Davis Centre"})

        assert value(app, "pathable_place_searches_total", mode="submit", outcome=outcome) == 1
        # The provider was asked, so its time is reported whatever it answered.
        assert value(app, "pathable_place_search_duration_seconds_count", mode="submit") == 1

    def test_searching_as_somebody_types_is_its_own_mode(
        self, app: FastAPI, client: TestClient
    ) -> None:
        app.state.geocoder = _Provider(FOUND)

        client.post(SEARCH, json={"query": "Dav", "autocomplete": True})

        assert (
            value(app, "pathable_place_searches_total", mode="autocomplete", outcome="success") == 1
        )

    def test_an_unknown_region_and_an_unparsed_body_are_invalid(
        self, app: FastAPI, client: TestClient
    ) -> None:
        assert client.post(SEARCH, json={"region": "atlantis", "query": "x"}).status_code == 404
        assert client.post(SEARCH, json={"query": ""}).status_code == 422

        assert value(app, "pathable_place_searches_total", mode="submit", outcome="invalid") == 1
        assert value(app, "pathable_place_searches_total", mode=UNPARSED, outcome="invalid") == 1


class TestPrivacy:
    """Nothing about who asked, or where, reaches a label."""

    def _send_everything(self, app: FastAPI, client: TestClient) -> None:
        identifying = {"X-Forwarded-For": SECRET_FORWARDED_FOR, "X-Request-ID": SECRET_REQUEST_ID}
        app.state.geocoder = _Provider(FOUND)
        client.post(SEARCH, json={"query": SECRET_QUERY}, headers=identifying)
        client.post(SEARCH, json={"query": SECRET_QUERY, "autocomplete": True})
        client.post(COMPARE, json=route_body(), headers=identifying)
        client.post(COMPARE, json=route_body(profile="custom"))
        client.post(f"{COMPARE}?lat={SECRET_LATITUDE}", json=route_body())
        client.get(f"/api/v1/places/{SECRET_QUERY}")

    def test_no_coordinate_query_address_or_request_id_is_exported(
        self, app: FastAPI, client: TestClient
    ) -> None:
        self._send_everything(app, client)
        text = exposition(app)

        for secret in (
            str(SECRET_LATITUDE),
            str(SECRET_LONGITUDE),
            "4712345678",
            "SECRETQUERY",
            "Erb Street",
            SECRET_FORWARDED_FOR,
            "203.0.113",
            "SECRETID",
            "testclient",
        ):
            assert secret not in text, f"{secret!r} reached the exposition"

    def test_every_label_value_comes_from_a_closed_vocabulary(
        self, app: FastAPI, client: TestClient
    ) -> None:
        self._send_everything(app, client)
        templates = {template.path for template in route_templates(app)}
        statuses = {str(code) for code in HTTP_STATUSES} | {"1xx", "2xx", "3xx", "4xx", "5xx"}
        allowed: dict[str, set[str]] = {
            "method": set(HTTP_METHODS) | {OTHER},
            "route": templates | {UNMATCHED_ROUTE},
            "status": statuses | {OTHER},
            "status_class": {"1xx", "2xx", "3xx", "4xx", "5xx", OTHER},
            "profile": set(ROUTE_PROFILES) | {UNPARSED, OTHER},
            "outcome": {"success", "no_route", "empty", "not_searched", "invalid", "error"},
            "kind": {"shortest", "profile"},
            "mode": {"autocomplete", "submit", UNPARSED},
            "region": {"waterloo"},
            "stage": {"preload", "load"},
            "version": {VERSION},
        }

        for family in text_string_to_metric_families(exposition(app)):
            if not family.name.startswith("pathable_"):
                continue
            for sample in family.samples:
                for label, label_value in sample.labels.items():
                    if label == "le":
                        float(label_value)  # a bucket bound, always a number
                        continue
                    assert label in allowed, f"{sample.name} has unexpected label {label!r}"
                    assert label_value in allowed[label], (
                        f"{sample.name}{{{label}={label_value!r}}} is outside its vocabulary"
                    )


class TestNeverInTheWay:
    """A broken collector costs the metric, never the request."""

    class _Broken:
        def labels(self, **_: str) -> Any:
            msg = "collector is broken"
            raise RuntimeError(msg)

    def test_routing_still_answers_when_its_metrics_fail(
        self, app: FastAPI, client: TestClient
    ) -> None:
        metrics: Metrics = app.state.metrics
        metrics.route_comparisons = self._Broken()  # type: ignore[assignment]
        metrics.http_requests = self._Broken()  # type: ignore[assignment]

        response = client.post(COMPARE, json=route_body())

        # The answer this application gives without a database, not a 500.
        assert response.status_code == 503
        assert response.json()["code"] == "database_unavailable"

    def test_search_still_answers_when_its_metrics_fail(
        self, app: FastAPI, client: TestClient
    ) -> None:
        metrics: Metrics = app.state.metrics
        metrics.place_searches = self._Broken()  # type: ignore[assignment]
        metrics.place_search_duration = self._Broken()  # type: ignore[assignment]
        app.state.geocoder = _Provider(FOUND)

        response = client.post(SEARCH, json={"query": "Davis Centre"})

        assert response.status_code == 200
        assert response.json()["matches"][0]["label"] == "Davis Centre"

    def test_a_failure_is_logged_once_not_once_per_request(
        self, app: FastAPI, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        metrics: Metrics = app.state.metrics
        metrics.http_requests = self._Broken()  # type: ignore[assignment]

        with caplog.at_level(logging.ERROR, logger="pathable_api.core.metrics"):
            for _ in range(5):
                assert client.get(LIVE).status_code == 200

        failures = [r for r in caplog.records if "Recording a metric failed" in r.getMessage()]
        assert len(failures) == 1

    def test_a_label_that_cannot_be_resolved_is_unmatched(
        self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(*_: Any) -> str:
            msg = "router is broken"
            raise RuntimeError(msg)

        monkeypatch.setattr(middleware_module, "route_template", broken)

        assert client.get(LIVE).status_code == 200
        assert value(app, "pathable_http_requests_total", route=UNMATCHED_ROUTE) == 1


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def _scrape(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
        body: bytes = response.read()
        return body.decode()


class TestExposition:
    def test_the_listener_serves_the_registry_and_closes(self) -> None:
        metrics = Metrics(version=VERSION)
        server = serve_metrics(metrics, host="127.0.0.1", port=0)
        try:
            text = _scrape(server.port)
        finally:
            server.close()

        assert f'pathable_build_info{{version="{VERSION}"}} 1.0' in text
        with pytest.raises(urllib.error.URLError):
            _scrape(server.port)

    def test_the_public_application_does_not_serve_metrics(self, client: TestClient) -> None:
        # Caddy proxies /api/v1/* to the API; neither path may answer with the
        # exposition, which has no authentication by design.
        assert client.get("/metrics").status_code == 404
        assert client.get("/api/v1/metrics").status_code == 404

    def test_the_lifespan_serves_metrics_on_their_own_port_only_while_running(self) -> None:
        port = _free_port()
        app = create_app(build_settings(metrics_port=port))

        with TestClient(app) as client:
            client.get(LIVE)
            text = _scrape(port)

        assert 'pathable_http_requests_total{method="GET",route="/api/v1/health/live"' in text
        with pytest.raises(urllib.error.URLError):
            _scrape(port)

    def test_without_a_port_nothing_listens(self) -> None:
        assert build_settings().metrics_port is None


class TestConfiguration:
    def test_an_empty_metrics_port_means_no_listener(self) -> None:
        # Regression: `METRICS_PORT=`, as .env.example ships it, failed integer
        # parsing, so a developer copying the example could not start the API.
        assert settings_from({"metrics_port": ""}).metrics_port is None
        assert settings_from({"metrics_port": "  "}).metrics_port is None
        assert settings_from({"metrics_port": "9464"}).metrics_port == 9464

    def test_the_metrics_port_cannot_be_the_api_port(self) -> None:
        with pytest.raises(ValueError, match="METRICS_PORT must differ from API_PORT"):
            build_settings(api_port=8000, metrics_port=8000)

    def test_an_out_of_range_port_is_refused(self) -> None:
        with pytest.raises(ValueError, match="metrics_port"):
            build_settings(metrics_port=70000)
