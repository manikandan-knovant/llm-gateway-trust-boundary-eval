"""Synthetic clinical notes covering the HIPAA Safe Harbor categories (E8, also used by E7 and E12). SYNTHETIC ONLY.

Every value is invented. Where a format must look real, it uses ranges set aside for documentation or testing:
phones 555-0100..0199, e-mail at example.com/.org/.net, IP addresses in the documentation ranges 192.0.2.0/24,
198.51.100.0/24, 203.0.113.0/24, published test card numbers and the textbook example IBANs.
SSNs: random digits in the valid SSN format (the gateway's SSN rule deliberately ignores the reserved areas 000, 666
and 9xx, so reserved values could not test it); they are not taken from any person.

Each PHI note holds 5 planted identifiers from 5 different categories; across 300 notes the 21 category keys appear
71 or 72 times each (1,500 slots), and each category's formats rotate evenly. Spans are exact (start, end) of the identifier value only;
labels such as "MRN:" are not part of the value. PHI-free notes (ids "neg-*") contain none of these categories but
use wording that detectors confuse with identifiers (weekdays, relative dates, doses, lab values, ages under 90).
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field

GIVEN = ["Maria", "James", "Aisha", "Daniel", "Elena", "Samuel", "Priya", "Thomas", "Grace", "Omar", "Hannah",
         "Lucas", "Fatima", "Noah", "Chloe", "Victor", "Irene", "Marcus", "Leila", "Peter", "Rosa", "Kenji"]
FAMILY = ["Alvarez", "Brennan", "Okafor", "Lindqvist", "Moreau", "Castillo", "Nakamura", "Whitfield", "Haddad",
          "Kowalski", "Fernsby", "Delacroix", "Abernathy", "Quintero", "Sorensen", "Mbeki", "Hollister", "Varga",
          "Ellison", "Thorne", "Galloway", "Petrovic"]
STREETS = ["Elm Street", "Harbor Rd", "W. Lincoln Ave", "Maple Ct.", "Ridgeview Drive", "Orchard Lane",
           "N. 5th St", "Birchwood Blvd"]
CITIES = ["Springfield", "Riverton", "Fairview", "Lakewood", "Greenville", "Brookhaven", "Maplewood", "Ashford"]
COUNTIES = ["Lake County", "Marion County", "Franklin County", "Clay County", "Polk County", "Warren County"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
TEST_CARDS = ["4111 1111 1111 1111", "4012888888881881", "5555 5555 5555 4444", "3782 822463 10005"]
TEST_IBANS = ["GB82 WEST 1234 5698 7654 32", "DE89 3704 0044 0532 0130 00"]
AREAS = ["212", "312", "415", "617", "702", "808", "919", "503"]

FILLER = ["Reports improved sleep.", "BP 128/84, HR 72.", "Continue metformin 500 mg BID.", "Lungs clear bilaterally.",
          "Denies chest pain.", "Labs reviewed with the patient.", "Plan discussed at length.", "No acute distress.",
          "Tolerating diet.", "Pain 3/10 today.", "HbA1c 7.2%.", "Will repeat CBC.", "Afebrile overnight."]
FRAMES = ["Progress note.", "Referral letter.", "Phone message:", "Discharge summary excerpt.", "Pt msg:",
          "Nursing note.", "Clinic visit.", "Case review for the team."]
NEGATIVE_SENTENCES = [
    "Seen on Monday for follow-up of type 2 diabetes.", "Next week we will recheck the lipid panel.",
    "Take lisinopril 10 mg once daily and atorvastatin 40 mg nightly.", "eGFR 58, LDL 130 mg/dL, INR 2.4.",
    "A 54-year-old with chronic low back pain, diagnosed in 2019.", "Return in 3 months or sooner if symptoms worsen.",
    "Discussed with the attending; MRI of the lumbar spine ordered.", "Tylenol 650 mg q6h PRN for pain.",
    "Temp 37.8 C, O2 sat 96% on room air.", "Patient in Room 12, bed B, since yesterday.",
    "ICD-10 E11.9; CPT 99213 for today's visit.", "Twice daily dosing was confusing, so we simplified it.",
    "Symptoms started 2 days ago and improved by Tuesday.", "Weight down 3 kg over the past month.",
    "Refer to physical therapy twice a week for six weeks.", "The clinic will call tomorrow with results.",
    "A 72-year-old woman with atrial fibrillation on apixaban 5 mg BID.", "Vaccinated for influenza this season.",
    "Follow-up appointment every other week until stable.", "Glucose log shows fasting values of 110 to 140.",
]


@dataclass(frozen=True)
class Entity:
    type: str      # category key
    value: str
    fmt: str
    start: int
    end: int


@dataclass
class Note:
    id: str
    text: str
    entities: list[Entity] = field(default_factory=list)
    negative: bool = False


def _digits(rng, n):
    return "".join(rng.choice(string.digits) for _ in range(n))


def _luhn_ok(num: str) -> bool:
    d = [int(c) for c in num if c.isdigit()]
    s = 0
    for i, x in enumerate(reversed(d)):
        if i % 2:
            x = x * 2 - 9 if x * 2 > 9 else x * 2
        s += x
    return s % 10 == 0


def _dea(rng) -> str:
    letters = rng.choice("ABFGM") + rng.choice(string.ascii_uppercase)
    d = [rng.randint(0, 9) for _ in range(6)]
    check = (d[0] + d[2] + d[4] + 2 * (d[1] + d[3] + d[5])) % 10
    return letters + "".join(map(str, d)) + str(check)


def _vin(rng) -> str:
    chars = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    return "".join(rng.choice(chars) for _ in range(17))


def _ssn(rng) -> tuple[str, str, str]:
    area = rng.randint(100, 665)
    if area == 666:
        area = 667
    return f"{area:03d}", f"{rng.randint(1, 99):02d}", f"{rng.randint(1, 9999):04d}"


# category key -> (row label, Safe Harbor number, expected placeholder types, formats)
# each format: (format name, value maker(rng) -> value, list of fragment templates with {v})
def _categories():
    def name_full(r): return f"{r.choice(GIVEN)} {r.choice(FAMILY)}"
    def name_lastfirst(r): return f"{r.choice(FAMILY).upper()}, {r.choice(GIVEN).upper()}"
    def name_initial(r): return f"{r.choice(GIVEN)} {r.choice(FAMILY)[0]}."
    def date_fmt(kind):
        def make(r):
            y, m, d = r.randint(1931, 2024), r.randint(1, 12), r.randint(1, 28)
            return {"us_slash": f"{m:02d}/{d:02d}/{y}", "iso": f"{y}-{m:02d}-{d:02d}",
                    "long": f"{MONTHS[m - 1]} {d}, {y}", "day_mon": f"{d} {MONTHS[m - 1][:3]} {y}",
                    "ordinal": f"{MONTHS[m - 1]} {d}{'th' if d > 3 else ['st', 'nd', 'rd'][d - 1]}, {y}",
                    "dotted": f"{m:02d}.{d:02d}.{y}", "month_year": f"{MONTHS[m - 1]} {y}"}[kind]
        return make
    def phone_fmt(kind):
        def make(r):
            a, last = r.choice(AREAS), f"01{r.randint(0, 99):02d}"
            return {"paren": f"({a}) 555-{last}", "dash": f"{a}-555-{last}", "dot": f"{a}.555.{last}",
                    "intl": f"+1 {a} 555 {last}", "spaces": f"{a} 555 {last}"}[kind]
        return make
    return {
        "NAME": ("Names", "1", {"PERSON"}, [
            ("full", name_full, ["Patient {v} seen today.", "Spoke with {v}, the daughter, by phone.",
                                 "{v} reports dizziness since last visit."]),
            ("last_first", name_lastfirst, ["Patient: {v}.", "Name on chart: {v}."]),
            ("initial", name_initial, ["Pt {v} here for follow-up.", "Seen with husband {v}."])]),
        "STREET": ("Geographic: street address", "2", {"LOCATION"}, [
            ("street", lambda r: f"{r.randint(10, 9899)} {r.choice(STREETS)}",
             ["Lives at {v}.", "Home address: {v}.", "Addr {v}, ground floor."])]),
        "CITY": ("Geographic: city", "2", {"LOCATION"}, [
            ("city", lambda r: r.choice(CITIES), ["Lives in {v}, OH.", "Moved to {v} last year.", "Transferred from {v}."])]),
        "COUNTY": ("Geographic: county", "2", {"LOCATION"}, [
            ("county", lambda r: r.choice(COUNTIES), ["Resides in {v}.", "{v} EMS transported the patient."])]),
        "ZIP": ("Geographic: ZIP code", "2", {"ZIP", "LOCATION"}, [
            ("zip_context", lambda r: f"{r.randint(10000, 99899)}", ["ZIP {v}.", "Zip code {v} on file."]),
            ("zip_bare", lambda r: f"{r.randint(10000, 99899)}", ["Mailing area Springfield, IL {v}.", "Area {v}."])]),
        "DATE": ("Dates (except year)", "3", {"DATE"}, [
            (k, date_fmt(k), ["DOB {v}.", "Admitted {v}.", "Discharged on {v}.", "Seen in clinic {v}."])
            for k in ("us_slash", "iso", "long", "day_mon", "ordinal", "dotted", "month_year")]),
        "AGE90": ("Ages over 89", "3", {"AGE"}, [
            ("age", lambda r: str(r.randint(90, 104)),
             ["A {v}-year-old woman with heart failure.", "Age {v}, lives alone.", "{v} y/o male, fall at home."])]),
        "PHONE": ("Telephone numbers", "4", {"PHONE"}, [
            (k, phone_fmt(k), ["Call {v} with results.", "Cell: {v}.", "Reach the daughter at {v}."])
            for k in ("paren", "dash", "dot", "intl", "spaces")]),
        "FAX": ("Fax numbers", "5", {"PHONE"}, [
            (k, phone_fmt(k), ["Fax {v}.", "Send records to fax number {v}."]) for k in ("paren", "dash")]),
        "EMAIL": ("E-mail addresses", "6", {"EMAIL"}, [
            ("plain", lambda r: f"{r.choice(GIVEN).lower()}.{r.choice(FAMILY).lower()}@example.com",
             ["Email {v}.", "Contact: {v}."]),
            ("plus", lambda r: f"{r.choice(GIVEN).lower()}_{r.randint(10, 99)}@mail.example.org", ["E-mail {v}."]),
            ("obfuscated", lambda r: f"{r.choice(GIVEN).lower()} at example dot net", ["Email is {v}."])]),
        "SSN": ("Social Security numbers", "7", {"SSN"}, [
            ("dashes", lambda r: "-".join(_ssn(r)), ["SSN {v}.", "Social security number {v}."]),
            ("spaces", lambda r: " ".join(_ssn(r)), ["SS# {v}."]),
            ("plain", lambda r: "".join(_ssn(r)), ["SSN: {v}."])]),
        "MRN": ("Medical record numbers (employer-specific label)", "8", {"MRN"}, [
            ("mrn_colon", lambda r: _digits(r, 7), ["MRN: {v}."]), ("mrn_hash", lambda r: _digits(r, 8), ["MRN#{v}."]),
            ("mrn_words", lambda r: _digits(r, 7), ["Medical record number {v}."]),
            ("med_rec", lambda r: _digits(r, 7), ["Med Rec # {v}."]),
            ("chart", lambda r: _digits(r, 7), ["Chart #{v}."]), ("mr_hash", lambda r: _digits(r, 7), ["MR# {v}."])]),
        "MEMBER": ("Health plan beneficiary numbers (member ID, employer-specific label)", "9", {"MEMBER_ID"}, [
            ("member_m", lambda r: "M" + _digits(r, 9), ["Member ID {v}.", "Member #: {v}."]),
            ("subscriber", lambda r: _digits(r, 9), ["Subscriber ID {v}."]),
            ("insurance", lambda r: "XQZ" + _digits(r, 9), ["Insurance ID {v}."]),
            ("medicaid", lambda r: _digits(r, 8) + "A", ["Medicaid ID {v}."])]),
        "ACCOUNT": ("Account numbers", "10", {"ACCOUNT"}, [
            ("card", lambda r: r.choice(TEST_CARDS), ["Card {v} on file for copay."]),
            ("bank", lambda r: _digits(r, 10), ["Account number {v}.", "Bank acct {v}."]),
            ("iban", lambda r: r.choice(TEST_IBANS), ["IBAN {v}."])]),
        "LICENSE": ("Certificate / license numbers", "11", {"LICENSE"}, [
            ("drivers", lambda r: r.choice(string.ascii_uppercase) + _digits(r, 7), ["Driver's license {v}.", "DL# {v}."]),
            ("passport", lambda r: _digits(r, 9), ["Passport no. {v}."]),
            ("dea", _dea, ["Prescriber DEA {v}."]),
            ("medical", lambda r: "MD" + _digits(r, 6), ["Medical license number {v}."])]),
        "VEHICLE": ("Vehicle identifiers", "12", {"VIN", "VEHICLE_ID"}, [
            ("vin", _vin, ["VIN {v}.", "Vehicle identification number {v}."]),
            ("plate", lambda r: f"{r.randint(1, 9)}{''.join(r.choice(string.ascii_uppercase) for _ in range(3))}{_digits(r, 3)}",
             ["License plate {v}.", "Car plate {v} in the lot."])]),
        "DEVICE": ("Device identifiers / serial numbers", "13", {"DEVICE_ID"}, [
            ("serial", lambda r: f"SN-{_digits(r, 5)}-{''.join(r.choice(string.ascii_uppercase) for _ in range(2))}",
             ["Pacemaker serial {v}.", "Implant serial number {v}."]),
            ("pump", lambda r: f"{_digits(r, 4)}-{_digits(r, 4)}-{_digits(r, 2)}", ["Insulin pump S/N {v}."]),
            ("udi", lambda r: f"(01)0088483{_digits(r, 7)}", ["Device UDI {v}."])]),
        "URL": ("Web URLs", "14", {"URL"}, [
            ("https", lambda r: f"https://portal.example.org/patients/{_digits(r, 5)}", ["Results at {v}."]),
            ("http", lambda r: f"http://www.example.com/u/{r.choice(FAMILY).lower()}-{_digits(r, 3)}",
             ["Photo uploaded to {v}."]),
            ("bare", lambda r: f"portal.example.net/chart/{_digits(r, 5)}", ["See {v} for the chart."])]),
        "IP": ("IP addresses", "15", {"IP"}, [
            ("ipv4", lambda r: r.choice(["192.0.2.", "198.51.100.", "203.0.113."]) + str(r.randint(1, 254)),
             ["Logged in from {v}.", "Device IP address {v}."])]),
        "BIOMETRIC": ("Biometric identifiers", "16", {"BIOMETRIC"}, [
            ("fingerprint", lambda r: f"FP-{_digits(r, 4)}-{_digits(r, 4)}", ["Fingerprint template ID {v}."]),
            ("voiceprint", lambda r: f"VP-{_digits(r, 5)}", ["Voiceprint ID {v}."])]),
        "CLAIM": ("Other unique numbers: claim number (employer-specific label)", "18", {"CLAIM_NUMBER"}, [
            ("claim_c", lambda r: f"C-{r.randint(2019, 2026)}-{_digits(r, 5)}", ["Claim number {v}."]),
            ("claim_hash", lambda r: f"{r.randint(2019, 2026)}-{_digits(r, 6)}", ["Claim # {v}."]),
            ("claim_no", lambda r: _digits(r, 9), ["Claim no. {v}."]),
            ("claim_ref", lambda r: "CLM" + _digits(r, 8), ["Claim ref {v}."])]),
    }


CATEGORIES = _categories()
ORDER = list(CATEGORIES)


def generate(n_phi: int = 300, n_neg: int = 100, seed: int = 8) -> list[Note]:
    rng = random.Random(seed)
    counters = {k: 0 for k in ORDER}
    notes: list[Note] = []
    per_note = 5
    slots = n_phi * per_note
    tokens = [ORDER[i % len(ORDER)] for i in range(slots)]     # every category floor/ceil(slots / categories) times
    rng.shuffle(tokens)
    deals: list[list[str]] = []
    for i in range(n_phi):                                      # deal 5 DIFFERENT categories per note
        hand = []
        for j in range(i * per_note, (i + 1) * per_note):
            if tokens[j] in hand:
                k = next((k for k in range(j + 1, slots) if tokens[k] not in hand), None)
                if k is None:   # near the end: swap with an earlier note, keeping both notes free of duplicates
                    k = next(k for k in range(0, i * per_note) if tokens[k] not in hand
                             and tokens[j] not in deals[k // per_note])
                    deals[k // per_note][k % per_note] = tokens[j]
                tokens[j], tokens[k] = tokens[k], tokens[j]
            hand.append(tokens[j])
        deals.append(hand)
    for i in range(n_phi):
        cats = deals[i]
        sentences: list[tuple[str, str | None, str, str]] = []   # (template, category, value, fmt)
        for cat in cats:
            _, _, _, fmts = CATEGORIES[cat]
            fname, make, templates = fmts[counters[cat] % len(fmts)]
            counters[cat] += 1
            sentences.append((rng.choice(templates), cat, make(rng), fname))
        for _ in range(rng.randint(1, 3)):
            sentences.append((rng.choice(FILLER), None, "", ""))
        rng.shuffle(sentences)
        text, ents = rng.choice(FRAMES), []
        for tmpl, cat, value, fname in sentences:
            text += " "
            if cat is None:
                text += tmpl
                continue
            before, _, after = tmpl.partition("{v}")
            text += before
            start = len(text)
            text += value
            ents.append(Entity(cat, value, fname, start, start + len(value)))
            text += after
        for e in ents:
            assert text[e.start:e.end] == e.value
        notes.append(Note(f"p{seed}-{i:03d}", text, ents))
    for i in range(n_neg):
        k = rng.randint(3, 5)
        text = rng.choice(FRAMES) + " " + " ".join(rng.sample(NEGATIVE_SENTENCES, k))
        notes.append(Note(f"neg{seed}-{i:03d}", text, [], negative=True))
    return notes


def card_numbers_pass_luhn() -> bool:
    return all(_luhn_ok(c) for c in TEST_CARDS)
