"""Unit tests for the E14 load helpers (no network, no Docker)."""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from e14_scale import MARK_RE, MRN_BASE, MRN_RE, foreign, prompt  # noqa: E402


def test_marker_has_no_eight_digit_run_and_values_are_unique():
    seen_mrn, seen_marker = set(), set()
    for seq in range(1, 3001):
        text, mrn, marker = prompt("a", 4, 128, seq % 128, seq // 128, seq, MRN_BASE["a"][4])
        assert not re.search(r"\d{8}", marker)                 # the E9 marker artefact cannot recur
        assert MRN_RE.findall(text) == [mrn]                   # the only MRN-shaped number is the MRN
        assert MARK_RE.findall(text) == [marker]
        seen_mrn.add(mrn)
        seen_marker.add(marker)
    assert len(seen_mrn) == len(seen_marker) == 3000


def test_mrn_ranges_do_not_overlap():
    bases = sorted([*MRN_BASE["a"].values(), MRN_BASE["b"], MRN_BASE["c"]])
    assert all(b - a >= 1_000_000 for a, b in zip(bases, bases[1:]))


def test_foreign_counts_only_other_requests_values():
    t1, m1, k1 = prompt("b", 2, 32, 0, 0, 1, MRN_BASE["b"])
    t2, m2, k2 = prompt("b", 2, 32, 1, 0, 2, MRN_BASE["b"])
    mrns, markers = {m1, m2}, {k1, k2}
    assert foreign(t1, m1, k1, mrns, markers) == 0             # own text: no foreign value
    assert foreign(t2, m1, k1, mrns, markers) == 2             # another request's MRN and marker
    assert foreign("MRN 49999999 REQ-e14b-w9-c9-k9x9", m1, k1, mrns, markers) == 0   # never issued: not counted
