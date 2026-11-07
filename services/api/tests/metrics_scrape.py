"""Reading an application's metrics the way a scrape of its metrics port would."""

from __future__ import annotations

from fastapi import FastAPI
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families

from pathable_api.core.metrics import Metrics


def exposition(app: FastAPI) -> str:
    """What a scrape of the metrics port would receive."""
    metrics: Metrics = app.state.metrics
    return generate_latest(metrics.registry).decode()


def samples(app: FastAPI, name: str) -> list[tuple[dict[str, str], float]]:
    """Every sample of one metric, as (labels, value)."""
    found: list[tuple[dict[str, str], float]] = []
    for family in text_string_to_metric_families(exposition(app)):
        for sample in family.samples:
            if sample.name == name:
                found.append((dict(sample.labels), sample.value))
    return found


def value(app: FastAPI, name: str, **labels: str) -> float:
    """The value of the one sample of ``name`` whose labels include ``labels``."""
    matching = [
        sample_value
        for sample_labels, sample_value in samples(app, name)
        if all(sample_labels.get(key) == wanted for key, wanted in labels.items())
    ]
    assert len(matching) == 1, f"{name}{labels}: {len(matching)} samples"
    return matching[0]
