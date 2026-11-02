"""The truth audit's own checks. An audit check that cannot fail is worse than none."""

from __future__ import annotations

from pathable_api.routing.engine import compute_route
from pathable_api.routing.profiles import STANDARD, get_profile
from pathable_api.routing.truth_audit import (
    audit_case,
    audit_profiles,
    check_hard_limits,
    check_wording,
    totals,
)
from pathable_api.routing.truth_corpus import AuditCase
from tests.unit.test_routing_units import journey, stair_network

WHEELCHAIR = get_profile("wheelchair")


class TestWording:
    def test_an_infinite_distance_in_a_sentence_is_a_defect(self) -> None:
        # Regression: open farmland in Waterloo answered "The origin is inf m
        # from the nearest mapped path", and the audit passed it.
        found = check_wording("The origin is inf m from the nearest mapped path.", "wheelchair")
        assert [(item.check, item.severity) for item in found] == [("wording", "defect")]

    def test_nan_is_caught_in_any_case(self) -> None:
        assert check_wording("+NaN m to avoid 1 recorded stairway.", None)

    def test_words_that_contain_the_letters_are_not(self) -> None:
        assert check_wording("Information is missing on 40 m of this infant path.", None) == []
        assert check_wording(None, None) == []


class TestHardLimits:
    def test_a_stairway_on_a_step_free_route_is_a_defect(self) -> None:
        n, ends = stair_network(step_count=12, pieces=2)
        origin, destination = journey(n, ends)
        # The shortest route climbs the stairs; held against the wheelchair
        # profile's hard limits it must fail, once per stair segment.
        shortest = compute_route(
            n.graph(), origin=origin, destination=destination, profile=STANDARD
        )

        found = check_hard_limits(shortest, WHEELCHAIR)
        assert [item.check for item in found] == ["hard_constraint", "hard_constraint"]
        assert all(item.severity == "defect" for item in found)

    def test_the_route_the_profile_chose_passes(self) -> None:
        n, ends = stair_network(step_count=12, pieces=2)
        origin, destination = journey(n, ends)
        route = compute_route(n.graph(), origin=origin, destination=destination, profile=WHEELCHAIR)

        assert check_hard_limits(route, WHEELCHAIR) == []


class TestAuditCase:
    def test_a_stairway_detour_audits_clean_under_every_profile(self) -> None:
        n, ends = stair_network(step_count=12, pieces=2)
        origin, destination = journey(n, ends)
        case = AuditCase(
            "stairs", "a stairway with a ramp round it", ("stairs",), origin, destination
        )

        outcome = audit_case(n.graph(), case, audit_profiles())

        assert [item for item in outcome.findings if item.severity == "defect"] == []
        assert outcome.standard_stairways == 1
        assert {result.profile for result in outcome.profiles} == {
            profile.key for profile in audit_profiles()
        }
        assert totals([outcome])["standard_invariant_cases"] == 1
