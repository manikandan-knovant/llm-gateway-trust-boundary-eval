"""Unit tests for the research-addition helpers (no network, no Docker, no dataset content)."""

from __future__ import annotations

import collections
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from e7_vendors import fate, raw_output, sent_user_text  # noqa: E402
from e9_load import prompt  # noqa: E402
from e11_asqphi import occurrences, parse  # noqa: E402
from notes5 import CATEGORIES, ORDER, card_numbers_pass_luhn, generate  # noqa: E402
from scoring import score_entities  # noqa: E402
from stats import wilson  # noqa: E402


def test_notes_spans_exact_balanced_and_distinct():
    notes = generate(300, 100, seed=8)
    phi = [n for n in notes if not n.negative]
    assert len(phi) == 300 and sum(n.negative for n in notes) == 100
    counts = collections.Counter(e.type for n in phi for e in n.entities)
    assert set(counts) == set(ORDER) and min(counts.values()) >= 71 and max(counts.values()) <= 72
    for n in phi:
        assert len({e.type for e in n.entities}) == 5
        for e in n.entities:
            assert n.text[e.start:e.end] == e.value
    assert all(not n.entities for n in notes if n.negative)


def test_notes_are_deterministic_and_use_reserved_ranges():
    a, b = generate(50, 10, seed=3), generate(50, 10, seed=3)
    assert [n.text for n in a] == [n.text for n in b]
    assert card_numbers_pass_luhn()
    for n in generate(300, 0, seed=8):
        for e in n.entities:
            if e.type in ("PHONE", "FAX"):
                d = re.sub(r"\D", "", e.value)[-7:]
                assert d[:3] == "555" and 100 <= int(d[3:]) <= 199
            if e.type == "EMAIL" and "@" in e.value:
                assert e.value.rsplit("@", 1)[1].endswith(("example.com", "example.org", "example.net"))
            if e.type == "IP":
                assert e.value.startswith(("192.0.2.", "198.51.100.", "203.0.113."))
            if e.type == "SSN":
                assert re.sub(r"\D", "", e.value)[:3] not in ("000", "666") and not e.value.startswith("9")


def test_every_category_has_expected_types_and_formats():
    for key, (label, sh, expected, formats) in CATEGORIES.items():
        assert label and sh and expected and formats


SAMPLE = """===QUERY===
Guidance for a patient, Jane Q., seen at Pine Clinic on May 2, 2024 (MRN: 123456)?
===PHI_TAGS===
{"identifier_type": "NAME", "value": "Jane Q."}
{"identifier_type": "GEOGRAPHIC_LOCATION", "value": "Pine Clinic"}
{"identifier_type": "MEDICAL_RECORD_NUMBER", "value": "123456"}
{"identifier_type": "DATE", "value": "May 3, 2024"}

===QUERY===
What is the first-line therapy for hypertension in a 55-year-old?
===PHI_TAGS===

"""


def test_asq_format_parser_on_a_synthetic_sample(tmp_path):
    p = tmp_path / "sample.txt"
    p.write_text(SAMPLE, encoding="utf-8")
    qs = parse(str(p))
    assert [len(q["tags"]) for q in qs] == [4, 0]
    occ, missing = occurrences(qs[0]["query"], qs[0]["tags"])
    assert [o.type for o in occ] == ["NAME", "GEOGRAPHIC_LOCATION", "MEDICAL_RECORD_NUMBER"]
    assert [m["value"] for m in missing] == ["May 3, 2024"]          # not verbatim in the query: reported, not guessed
    for o in occ:
        assert qs[0]["query"][o.start:o.end] == o.value
    received = qs[0]["query"].replace("Jane Q.", "<<PERSON_1>>").replace("123456", "<<MRN_1>>")
    scores, _, aligned = score_entities(qs[0]["query"], received, occ)
    assert aligned and [s.status for s in scores] == ["masked", "leaked", "masked"]


def test_placeholder_fate_and_payload_helpers():
    assert fate("<<MRN_1>>", "MRN: <<MRN_1>>") == "kept"
    assert fate("<<MRN_1>>", "MRN [MRN_1]") == "altered_restorable"
    assert fate("<<MRN_1>>", "MRN <<MRN 1>>") == "altered_unrestorable"
    assert fate("<<MRN_1>>", "the record") == "absent"
    assert sent_user_text({"messages": [{"role": "user", "content": [{"type": "text", "text": "a"},
                                                                     {"type": "text", "text": "b"}]}]}) == "ab"
    assert raw_output({"content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "x"}]}) == "x"
    assert raw_output({"choices": [{"message": {"content": "y"}}]}) == "y"


def test_load_prompts_carry_unique_mrns_and_markers():
    seen_mrn, seen_marker = set(), set()
    for seq in range(1, 2000):
        text, mrn = prompt("on", 8, seq, seq)
        assert mrn in text and mrn not in seen_mrn
        marker = re.search(r"REQ-[a-z]+-\d+-\d+", text).group(0)
        assert marker not in seen_marker
        seen_mrn.add(mrn)
        seen_marker.add(marker)


def test_wilson_reference_values():
    lo, hi = wilson(205, 220)
    assert lo == pytest.approx(0.8906, abs=5e-4) and hi == pytest.approx(0.9582, abs=5e-4)
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == 1.0
