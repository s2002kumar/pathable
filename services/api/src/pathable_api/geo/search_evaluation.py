"""Run the search-quality corpus against a built place index and record every answer."""

from __future__ import annotations

import statistics
import time
import uuid
from collections import Counter
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from pathable_api.geo.gazetteer_store import search_gazetteer
from pathable_api.geo.search_corpus import SearchCase

#: Results asked for per query: what the endpoint returns.
RESULTS_PER_QUERY = 5
#: Each query is timed this many times; the median is reported.
TIMED_RUNS = 3


async def evaluate_search(
    session: AsyncSession, build_id: uuid.UUID, corpus: Sequence[SearchCase]
) -> list[dict[str, Any]]:
    """Every query's results, the rank of the place meant, and how long it took."""
    records: list[dict[str, Any]] = []
    for case in corpus:
        timings: list[float] = []
        found = []
        for _ in range(TIMED_RUNS):
            started = time.perf_counter()
            found = await search_gazetteer(session, build_id, case.query, limit=RESULTS_PER_QUERY)
            timings.append((time.perf_counter() - started) * 1000.0)
        expected = set(case.expected)
        rank = next(
            (
                position
                for position, match in enumerate(found, start=1)
                if (match.osm_type, match.osm_id) in expected
            ),
            None,
        )
        records.append(
            {
                "query": case.query,
                "category": case.category,
                "meaning": case.meaning,
                "expected": [list(element) for element in case.expected],
                "within": case.within,
                "rank": rank,
                "passed": rank is not None and rank <= case.within,
                "median_ms": round(statistics.median(timings), 1),
                "results": [
                    {
                        "label": match.label,
                        "category": match.category,
                        "kind": match.kind.value,
                        "matched_on": match.matched_on,
                        "osm": [match.osm_type, match.osm_id],
                    }
                    for match in found
                ],
            }
        )
    return records


def summarise_search(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Counts a reader can check against the records."""
    by_category: dict[str, dict[str, int]] = {}
    for record in records:
        entry = by_category.setdefault(record["category"], {"queries": 0, "passed": 0})
        entry["queries"] += 1
        entry["passed"] += int(record["passed"])
    timings = sorted(record["median_ms"] for record in records)
    return {
        "queries": len(records),
        "passed": sum(1 for record in records if record["passed"]),
        "first_result_is_the_place_meant": sum(1 for record in records if record["rank"] == 1),
        "not_in_results": sum(1 for record in records if record["rank"] is None),
        "by_category": dict(sorted(by_category.items())),
        "matched_on_first_result": dict(
            sorted(
                Counter(
                    record["results"][0]["matched_on"] for record in records if record["results"]
                ).items()
            )
        ),
        "latency_ms": {
            "median": round(statistics.median(timings), 1) if timings else None,
            "max": timings[-1] if timings else None,
        },
    }
