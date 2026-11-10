"""Unit tests for the harness helpers (stats, scoring, synthetic prompts). No network, no Docker."""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring import align, residual_placeholders, restore_ok, score_entities, to_variant  # noqa: E402
from stats import paired_median_difference, percentile, summarize, wilson  # noqa: E402
from synthetic_prompts import MRN_FORMATS, generate, neutral_prompt  # noqa: E402


# ---------------------------------------------------------------- percentiles
def test_percentile_matches_numpy_linear_definition():
    xs = [15, 20, 35, 40, 50]
    assert percentile(xs, 0) == 15
    assert percentile(xs, 100) == 50
    assert percentile(xs, 50) == 35
    assert percentile(xs, 40) == pytest.approx(29.0)        # numpy.percentile(xs, 40) == 29.0
    assert percentile(list(range(1, 101)), 95) == pytest.approx(95.05)
    assert percentile(list(range(1, 101)), 99) == pytest.approx(99.01)


def test_percentile_order_independent_and_single_value():
    assert percentile([3, 1, 2], 50) == 2
    assert percentile([7], 95) == 7


def test_percentile_rejects_bad_input():
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1], 101)


def test_summarize_and_paired_difference():
    s = summarize([1, 2, 3, 4])
    assert s["n"] == 4 and s["median"] == 2.5 and s["mean"] == 2.5 and s["min"] == 1 and s["max"] == 4
    assert paired_median_difference([10, 20, 30], [12, 25, 31]) == 2
    with pytest.raises(ValueError):
        paired_median_difference([1], [1, 2])


# ---------------------------------------------------------------- Wilson
def test_wilson_known_values():
    lo, hi = wilson(205, 220)                                # computed independently (z = 1.95996)
    assert lo == pytest.approx(0.8906, abs=5e-4) and hi == pytest.approx(0.9582, abs=5e-4)
    lo, hi = wilson(250, 250)
    assert lo == pytest.approx(0.9849, abs=5e-4) and hi == 1.0
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=5e-4)
    lo, hi = wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=5e-4) and hi == pytest.approx(0.7634, abs=5e-4)


def test_wilson_properties():
    for k, n in [(1, 3), (17, 40), (99, 100)]:
        lo, hi = wilson(k, n)
        assert 0 <= lo <= k / n <= hi <= 1
    assert wilson(0, 0) == (0.0, 1.0)
    with pytest.raises(ValueError):
        wilson(5, 4)


# ---------------------------------------------------------------- scoring
class E:
    def __init__(self, type_, value, start, fmt="x"):
        self.type, self.value, self.start, self.end, self.fmt = type_, value, start, start + len(value), fmt


ORIG = "Patient Maria Alvarez (MRN: 1234567) called 212-555-0142."


def ents():
    return [E("PERSON", "Maria Alvarez", 8), E("MRN", "1234567", 28), E("PHONE", "212-555-0142", 44)]


def test_align_and_score_all_masked():
    received = "Patient <<PERSON_1>> (MRN: <<MRN_1>>) called <<PHONE_1>>."
    ranges = align(ORIG, received)
    assert [(ORIG[r.start:r.end], r.types) for r in ranges] == [
        ("Maria Alvarez", ("PERSON",)), ("1234567", ("MRN",)), ("212-555-0142", ("PHONE",))]
    scores, over, aligned = score_entities(ORIG, received, ents())
    assert aligned and [s.status for s in scores] == ["masked", "masked", "masked"] and over == []


def test_score_leak_partial_and_overmask():
    received = "<<WORD_1>> Maria <<PERSON_1>> (MRN: 1234567) called <<PHONE_1>>."
    scores, over, aligned = score_entities(ORIG, received, ents())
    assert aligned
    assert [s.status for s in scores] == ["partial", "leaked", "masked"]
    assert [ORIG[r.start:r.end] for r in over] == ["Patient"]


def test_score_label_inside_placeholder_counts_as_masked():
    received = "Patient <<PERSON_1>> (<<MRN_1>>) called <<PHONE_1>>."
    scores, _, _ = score_entities(ORIG, received, ents())
    assert [s.status for s in scores] == ["masked", "masked", "masked"]


def test_adjacent_placeholders_share_one_range():
    orig = "A Maria Alvarez B"
    ranges = align(orig, "A <<PERSON_1>><<PERSON_2>> B")
    assert len(ranges) == 1 and orig[ranges[0].start:ranges[0].end] == "Maria Alvarez"
    assert ranges[0].types == ("PERSON", "PERSON")


def test_unaligned_is_reported_not_guessed():
    scores, over, aligned = score_entities(ORIG, "something else entirely <<X_1>>", ents())
    assert not aligned and all(s.status == "unaligned" for s in scores)


def test_no_placeholders_identical_and_different():
    assert align(ORIG, ORIG) == []
    assert align(ORIG, ORIG + "!") is None
    scores, _, aligned = score_entities(ORIG, ORIG, ents())
    assert aligned and all(s.status == "leaked" for s in scores)


def test_variants_and_restore_helpers():
    t = "x <<PERSON_1>> y <<MRN_12>>"
    assert to_variant(t, "exact") == t
    assert to_variant(t, "single") == "x <PERSON_1> y <MRN_12>"
    assert to_variant(t, "brackets") == "x [PERSON_1] y [MRN_12]"
    assert to_variant(t, "bare") == "x PERSON_1 y MRN_12"
    assert to_variant(t, "stream_split") == t
    assert restore_ok("a", "a") and not restore_ok("a", "a ")
    assert residual_placeholders("ok <<PERSON_1>> and [MRN_2] and PHONE_3") == ["<<PERSON_1>>", "[MRN_2]", "PHONE_3"]
    assert residual_placeholders("Maria Alvarez, 212-555-0142") == []


# ---------------------------------------------------------------- generator
def test_generator_is_deterministic_and_spans_exact():
    a, b = generate(40, 11), generate(40, 11)
    assert [p.text for p in a] == [p.text for p in b]
    assert [p.text for p in generate(40, 12)] != [p.text for p in a]
    for p in a:
        assert {e.type for e in p.entities} == {"PERSON", "PHONE", "MRN", "DATE"}
        for e in p.entities:
            assert p.text[e.start:e.end] == e.value


def test_generator_formats_and_synthetic_ranges():
    ps = generate(40, 3)
    mrn_fmts = [e.fmt for p in ps for e in p.entities if e.type == "MRN"]
    assert set(mrn_fmts) == set(MRN_FORMATS) and mrn_fmts.count("mrn_words") == 10
    texts = " ".join(p.text for p in ps)
    for label in ("MRN: ", "MRN#", "MRN ", "medical record number "):
        assert label in texts
    for p in ps:
        phone = next(e.value for e in p.entities if e.type == "PHONE")
        digits = "".join(c for c in phone if c.isdigit())
        assert digits[3:6] == "555" and 100 <= int(digits[6:]) <= 199


def test_neutral_prompt_salt():
    assert neutral_prompt("abc") != neutral_prompt("abd") and neutral_prompt() == neutral_prompt()
    assert math.isfinite(len(neutral_prompt("x")))


def test_align_keeps_placeholder_tokens():
    scores, _, _ = score_entities(ORIG, "Patient <<PERSON_1>> (MRN: <<MRN_1>>) called <<PHONE_1>>.", ents())
    assert [s.placeholders for s in scores] == [("<<PERSON_1>>",), ("<<MRN_1>>",), ("<<PHONE_1>>",)]


def test_placeholder_fate_categories():
    from e4_real import placeholder_fate
    assert placeholder_fate("<<PERSON_1>>", "Call <<PERSON_1>> today") == "kept"
    assert placeholder_fate("<<PERSON_1>>", "Call [PERSON_1] today") == "altered_restorable"
    assert placeholder_fate("<<PERSON_1>>", "Call PERSON_1 today") == "altered_restorable"
    assert placeholder_fate("<<PERSON_1>>", "Call <<PERSON 1>> today") == "altered_unrestorable"
    assert placeholder_fate("<<PERSON_1>>", "Call the patient today") == "absent"
