"""Deterministic synthetic patient-style prompts (baseline build 2026-11-09). SYNTHETIC DATA ONLY.

Every value is invented:
- names: random pairs from small lists of common given names and surnames (no real person is meant);
- phones: area code + 555-0100..555-0199 (the range reserved for fiction);
- MRNs: random 7-digit numbers in four label formats: "MRN: n", "MRN#n", "MRN n", "medical record number n";
- dates of birth: random dates 1940-2000 in five formats.

Each prompt records the exact character span of each planted identifier, so scoring never guesses.
For MRNs the span is the NUMBER only (the label is not an identifier).

  python synthetic_prompts.py --seed 11 --n 3      # print examples
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass, field

GIVEN = ["Maria", "James", "Aisha", "Daniel", "Elena", "Samuel", "Priya", "Thomas", "Grace", "Omar",
         "Hannah", "Lucas", "Fatima", "Noah", "Chloe", "Victor", "Irene", "Marcus", "Leila", "Peter"]
FAMILY = ["Alvarez", "Brennan", "Okafor", "Lindqvist", "Moreau", "Castillo", "Nakamura", "Whitfield",
          "Haddad", "Kowalski", "Fernsby", "Delacroix", "Abernathy", "Quintero", "Sorensen", "Mbeki",
          "Hollister", "Varga", "Ellison", "Thorne"]
AREA = ["212", "312", "415", "617", "702", "808", "919", "503"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
MRN_FORMATS = ("mrn_colon", "mrn_hash", "mrn_space", "mrn_words")
DATE_FORMATS = ("us_slash", "iso", "long", "day_mon", "us_short")
PHONE_FORMATS = ("paren", "dash", "dot")

TEMPLATES = [
    "Patient {name} (DOB {dob}, {mrn}) called from {phone} about refilling a blood pressure prescription. "
    "Please rewrite this as a short note for the care team.",
    "Summarize for the nurse: {name}, born {dob}, {mrn}, reports mild dizziness since Monday. "
    "Best contact number is {phone}.",
    "Draft a reminder: {name} ({mrn}) has a follow-up for knee pain. Date of birth {dob}. "
    "Call back on {phone} to confirm.",
    "Rewrite in plain language: lab results for {name}, DOB {dob}, are within normal range. "
    "{mrn}. Patient phone {phone}.",
    "Create a one-line handoff: {name} with {mrn}, born on {dob}, needs a prior authorization call to {phone}.",
]


@dataclass(frozen=True)
class Entity:
    type: str      # PERSON | PHONE | MRN | DATE
    value: str     # the identifier exactly as planted
    fmt: str       # format label (MRN, DATE and PHONE formats; PERSON: "full_name")
    start: int
    end: int


@dataclass
class Prompt:
    id: str
    text: str
    entities: list[Entity] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({"id": self.id, "text": self.text, "entities": [asdict(e) for e in self.entities]})


def _date(rng: random.Random, fmt: str) -> str:
    y, m, d = rng.randint(1940, 2000), rng.randint(1, 12), rng.randint(1, 28)
    return {
        "us_slash": f"{m:02d}/{d:02d}/{y}",
        "iso": f"{y}-{m:02d}-{d:02d}",
        "long": f"{MONTHS[m - 1]} {d}, {y}",
        "day_mon": f"{d} {MONTHS[m - 1][:3]} {y}",
        "us_short": f"{m}/{d}/{y % 100:02d}",
    }[fmt]


def _phone(rng: random.Random, fmt: str) -> str:
    a, last = rng.choice(AREA), f"01{rng.randint(0, 99):02d}"
    return {"paren": f"({a}) 555-{last}", "dash": f"{a}-555-{last}", "dot": f"{a}.555.{last}"}[fmt]


def _mrn(rng: random.Random, fmt: str) -> tuple[str, str]:
    """(full text with label, number only)."""
    n = str(rng.randint(2_000_000, 9_899_999))
    label = {"mrn_colon": "MRN: ", "mrn_hash": "MRN#", "mrn_space": "MRN ", "mrn_words": "medical record number "}[fmt]
    return label + n, n


def generate(n: int, seed: int) -> list[Prompt]:
    """n prompts; formats cycle so each format appears about equally often (MRN 4, DATE 5, PHONE 3 formats)."""
    rng = random.Random(seed)
    out: list[Prompt] = []
    for i in range(n):
        mrn_fmt, date_fmt, phone_fmt = MRN_FORMATS[i % 4], DATE_FORMATS[i % 5], PHONE_FORMATS[i % 3]
        name = f"{rng.choice(GIVEN)} {rng.choice(FAMILY)}"
        dob = _date(rng, date_fmt)
        phone = _phone(rng, phone_fmt)
        mrn_text, mrn_number = _mrn(rng, mrn_fmt)
        template = TEMPLATES[rng.randrange(len(TEMPLATES))]
        values = {"name": name, "dob": dob, "phone": phone, "mrn": mrn_text}
        # Build the text piece by piece so every span is exact.
        text, entities, pos = "", [], 0
        for literal, key in _split(template):
            text += literal
            if key is None:
                continue
            start = len(text)
            text += values[key]
            if key == "name":
                entities.append(Entity("PERSON", name, "full_name", start, start + len(name)))
            elif key == "dob":
                entities.append(Entity("DATE", dob, date_fmt, start, start + len(dob)))
            elif key == "phone":
                entities.append(Entity("PHONE", phone, phone_fmt, start, start + len(phone)))
            elif key == "mrn":
                s = start + len(mrn_text) - len(mrn_number)
                entities.append(Entity("MRN", mrn_number, mrn_fmt, s, s + len(mrn_number)))
        for e in entities:
            assert text[e.start:e.end] == e.value, (text, e)
        out.append(Prompt(id=f"s{seed}-{i:03d}", text=text, entities=entities))
    return out


def _split(template: str) -> list[tuple[str, str | None]]:
    """'a {x} b' -> [('a ', 'x'), (' b', None)]."""
    parts, rest = [], template
    while "{" in rest:
        before, _, after = rest.partition("{")
        key, _, rest = after.partition("}")
        parts.append((before, key))
    parts.append((rest, None))
    return parts


def neutral_prompt(salt: str | None = None) -> str:
    """The short identifier-free prompt used for latency runs; salted so no cache can serve it.
    (An earlier wording ending "for next week" was masked as DATE by the detector, so it was replaced: checked
    2026-11-09, 0 entities in 5 salted tries.) Salts should start with a letter (see salt())."""
    base = "Summarize in one sentence: the clinic reviewed staffing options for the cardiology unit."
    return base if salt is None else f"{base} Ref {salt}."


def salt(rng: random.Random) -> str:
    """A 12-character salt that always starts with a letter (an all-digit string could look like an account
    number to the detector)."""
    return "q" + "".join(rng.choice("0123456789abcdef") for _ in range(11))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--n", type=int, default=3)
    a = ap.parse_args()
    for p in generate(a.n, a.seed):
        print(p.to_json())
