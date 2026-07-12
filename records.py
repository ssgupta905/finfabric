"""Synthetic records with a real ICAO 9303 TD1 MRZ (three 30-char lines,
genuine check digits). Names, addresses and countries are fictional; the
portrait in the renderer is two grey ellipses — nothing here needs to be
handled as personal data."""

from datetime import date, timedelta


SURNAMES = ["SHARMA", "KUMAR", "IYER", "NAIR", "BOSE", "REDDY", "MENON",
            "AGARWAL", "PATEL", "GUPTA", "SINGH", "VERMA", "RAO", "JOSHI",
            "MEHTA", "DAS", "KHAN", "DESAI"]
GIVENS = ["ARJUN", "PRIYA", "RAHUL", "ANJALI", "VIKRAM", "MEERA", "ADITYA",
          "SNEHA", "KARAN", "DIYA", "ROHAN", "KAVYA", "AMIT", "NEHA",
          "SIDDHARTH", "TARA"]
# ICAO 9303 three-letter country codes. Skewed to IND so the MRZ nationality
# field prints "IND" — realistic for an Indian bank's onboarding pipeline.
COUNTRIES = ["IND", "IND", "IND", "IND", "IND", "IND", "USA", "GBR", "SGP", "ARE"]
STREETS = ["MG ROAD", "MARINE DRIVE", "NEHRU NAGAR", "PARK STREET",
           "LINKING ROAD", "BANJARA HILLS", "ANNA SALAI", "SEC 18 MAIN"]
CITIES = ["MUMBAI", "BENGALURU", "DELHI", "CHENNAI", "KOLKATA", "HYDERABAD",
          "PUNE", "AHMEDABAD"]
# RBI KYC categories under the Master Direction on KYC (2016, as amended).
STATUSES = ["FULL KYC", "SIMPLIFIED KYC", "SMALL ACCOUNT", "BASIC SAVINGS",
            "CORPORATE KYC", "PERIODIC UPDATE DUE"]


def _mrz_char_value(c: str) -> int:
    if c == "<":
        return 0
    if c.isdigit():
        return int(c)
    if c.isalpha():
        return ord(c.upper()) - ord("A") + 10
    return 0


def _mrz_check(s: str) -> str:
    """ICAO 9303 check digit. Weights 7, 3, 1 repeating."""
    w = (7, 3, 1)
    total = sum(_mrz_char_value(c) * w[i % 3] for i, c in enumerate(s))
    return str(total % 10)


def _pad(s: str, n: int, ch: str = "<") -> str:
    return (s + ch * n)[:n]


def make_record(rng):
    surname = rng.choice(SURNAMES)
    given = rng.choice(GIVENS)

    dob = date(rng.randint(1965, 2005), rng.randint(1, 12), rng.randint(1, 28))
    issue = date.today() - timedelta(days=rng.randint(0, 3 * 365))
    expiry = issue + timedelta(days=rng.randint(3 * 365, 10 * 365))

    sex = rng.choice(["M", "F"])
    nat = rng.choice(COUNTRIES)
    street_no = rng.randint(1, 99)
    # Indian PIN codes are 6 digits, first digit 1-8.
    pin = f"{rng.randint(1, 8)}{rng.randint(10000, 99999)}"
    address = f"{street_no} {rng.choice(STREETS)}\n{rng.choice(CITIES)} {pin}"

    id_a = rng.randint(1000, 9999)
    id_b = rng.randint(1000, 9999)
    id_no = f"{id_a}-{id_b}"

    # RBI Master Direction: Full KYC valid for 10y (low risk), 8y (medium), 2y (high).
    period = f"{rng.choice([2, 8, 10])} YEARS"
    status = rng.choice(STATUSES)

    # TD1 layout: three lines of 30 characters. Field slice indices below are
    # what parse_mrz relies on; if you change the packing, change the parser.
    doc_num_mrz = _pad(f"{id_a}{id_b}", 9)
    doc_check = _mrz_check(doc_num_mrz)
    optional_l1 = "<" * 15
    line1 = _pad(f"I<{nat}{doc_num_mrz}{doc_check}{optional_l1}", 30)

    dob_mrz = f"{dob.year % 100:02d}{dob.month:02d}{dob.day:02d}"
    dob_check = _mrz_check(dob_mrz)
    exp_mrz = f"{expiry.year % 100:02d}{expiry.month:02d}{expiry.day:02d}"
    exp_check = _mrz_check(exp_mrz)
    optional_l2 = "<" * 11
    line2_no_overall = f"{dob_mrz}{dob_check}{sex}{exp_mrz}{exp_check}{nat}{optional_l2}"
    line2_no_overall = _pad(line2_no_overall, 29)
    # Composite check per ICAO 9303 TD1: cols 6-30 of line1 + 1-7,9-15,19-29 of line2.
    composite = line1[5:30] + line2_no_overall[0:7] + line2_no_overall[8:15] + line2_no_overall[18:29]
    line2 = line2_no_overall + _mrz_check(composite)

    name_mrz = f"{surname}<<{given}"
    line3 = _pad(name_mrz, 30)

    return {
        "name": f"{surname} {given}",
        "date_of_birth": dob.isoformat(),
        "sex": sex,
        "nationality": nat,
        "address": address,
        "status": status,
        "period_of_stay": period,
        "id_no": id_no,
        "date_of_issue": issue.isoformat(),
        "date_of_expiry": expiry.isoformat(),
        "_mrz": [line1, line2, line3],
    }
