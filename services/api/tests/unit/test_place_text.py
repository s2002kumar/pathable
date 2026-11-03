"""How place names and queries are compared.

The rule under test is symmetry: whatever a stored name becomes, the same words
typed as a query become the same thing, so the two meet.
"""

from __future__ import annotations

import re

from pathable_api.geo.place_text import (
    MAX_QUERY_TERMS,
    code_of,
    compact_query,
    initialism,
    normalise_text,
    query_terms,
)


class TestNormalisation:
    def test_abbreviated_and_written_out_addresses_meet(self) -> None:
        assert normalise_text("200 University Ave. W") == normalise_text(
            "200 University Avenue West"
        )

    def test_a_street_suffix_and_direction_are_expanded(self) -> None:
        assert normalise_text("King St N") == "king street north"

    def test_accents_do_not_stop_a_match(self) -> None:
        assert normalise_text("Café Pyrus") == "cafe pyrus"

    def test_an_apostrophe_does_not_split_a_word(self) -> None:
        # Regression guard: treating the apostrophe as a space would leave a
        # stray "s", which the abbreviation table reads as "south".
        assert normalise_text("Children's Museum") == "childrens museum"
        assert normalise_text("Children\u2019s Museum") == "childrens museum"

    def test_an_ampersand_is_the_word_and(self) -> None:
        assert normalise_text("Arts & Crafts") == "arts and crafts"

    def test_the_output_needs_no_escaping_in_a_pattern(self) -> None:
        # The search builds regular expressions from this text without escaping.
        hostile = "a.b*c(d)[e]{f}|g^h$i\\j%k_l'm\"n;--o"
        assert re.fullmatch(r"[a-z0-9 ]*", normalise_text(hostile))

    def test_blank_text_normalises_to_nothing(self) -> None:
        assert normalise_text("  ,.;  ") == ""


class TestQueryTerms:
    def test_words_keep_their_order_without_repeats(self) -> None:
        assert query_terms("King St N King") == ("king", "street", "north")

    def test_a_runaway_query_is_cut_short(self) -> None:
        words = " ".join(f"word{index}" for index in range(30))
        assert len(query_terms(words)) == MAX_QUERY_TERMS

    def test_punctuation_alone_has_no_terms(self) -> None:
        assert query_terms("!!! ???") == ()


class TestSpellings:
    def test_center_and_centre_meet(self) -> None:
        # "Davis Center" found the library and the taxi stand but not the
        # Davis Centre building itself.
        assert normalise_text("Davis Center") == normalise_text("Davis Centre") == "davis centre"
        assert normalise_text("Theater") == normalise_text("Theatre")


class TestCodes:
    def test_building_codes_are_recognised_as_written(self) -> None:
        assert [code_of(value) for value in ("DC", "slc", "E5", "EV3", "P.A.C.")] == [
            "dc",
            "slc",
            "e5",
            "ev3",
            "pac",
        ]

    def test_things_that_are_not_codes_are_not(self) -> None:
        assert [code_of(value) for value in ("85", "A", "Highway 7", "ABCDE1", None, "")] == [
            None
        ] * 6

    def test_a_query_compacts_as_typed_without_expanding_abbreviations(self) -> None:
        # "St" is a street suffix in a name and could be a code as typed.
        assert compact_query("St") == "st"
        assert compact_query("P.A.C.") == "pac"
        assert compact_query("E 5") == "e5"
        assert compact_query("Café") == "cafe"


class TestInitialisms:
    def test_initials_skip_small_words_and_keep_numbers(self) -> None:
        assert initialism("Student Life Centre") == "slc"
        assert initialism("Faculty of Mathematics") == "fm"
        assert initialism("Mathematics & Computer") == "mc"
        assert initialism("Engineering 5") == "e5"

    def test_a_single_word_or_a_very_long_name_has_none(self) -> None:
        assert initialism("Davis") is None
        assert initialism("one two three four five six seven") is None
