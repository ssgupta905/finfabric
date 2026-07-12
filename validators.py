"""Deterministic validators. Each returns (ok, canonical_value, why).
Canonical form is what gets committed to the Merkle leaf — two OCR strings
that mean the same thing must produce identical canonical output or the
dual-engine agreement signal is worthless."""

import re


def canon(s) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).strip().upper())


def loose_eq(a, b) -> bool:
    """Alphanumeric-only equality. The MRZ substitutes '<' for punctuation
    it cannot carry (hyphens, apostrophes), so `AL-RASHID` in the visual
    field and `AL<RASHID` in the MRZ must compare equal without either side
    winning the punctuation argument."""
    scrub = lambda x: re.sub(r"[^A-Z0-9]", "", canon(x))
    return scrub(a) == scrub(b)


def _v_text(s):
    c = canon(s)
    if not c:
        return False, c, "empty"
    return True, c, ""


def _v_date(s):
    c = canon(s).replace(" ", "")
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})$", c)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{2})-?(\d{2})-?(\d{4})$", c)
        if not m:
            return False, "", "bad date format"
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
        return False, "", "date out of range"
    return True, f"{y:04d}-{mo:02d}-{d:02d}", ""


def _make_enum(choices_str):
    allowed = {canon(c) for c in choices_str.split(",")}
    def check(s):
        c = canon(s)
        if c not in allowed:
            return False, c, f"not in {sorted(allowed)}"
        return True, c, ""
    return check


def _v_id(s):
    c = canon(s).replace(" ", "")
    m = re.match(r"^(\d{4})-?(\d{4})$", c)
    if not m:
        return False, "", "bad id format"
    return True, f"{m.group(1)}-{m.group(2)}", ""


VALIDATORS = {
    "text":     _v_text,
    "date":     _v_date,
    "enum:M,F": _make_enum("M,F"),
    "id_regex": _v_id,
}


def parse_mrz(lines):
    """Parse a TD1 MRZ (three 30-char lines) and verify check digits.
    Returns (ok, {field_name: value}, [errors])."""
    if not lines or len(lines) != 3:
        return False, {}, ["wrong number of MRZ lines"]

    l1, l2, l3 = [str(x) for x in lines]
    if not all(len(x) == 30 for x in (l1, l2, l3)):
        return False, {}, ["MRZ lines wrong length"]

    from records import _mrz_check as chk
    errs = []

    nat = l1[2:5]
    doc_num = l1[5:14]
    doc_check = l1[14]
    if chk(doc_num) != doc_check:
        errs.append("doc number check digit fail")

    dob_raw, dob_chk = l2[0:6], l2[6]
    sex = l2[7]
    exp_raw, exp_chk = l2[8:14], l2[14]
    if chk(dob_raw) != dob_chk:
        errs.append("DOB check digit fail")
    if chk(exp_raw) != exp_chk:
        errs.append("expiry check digit fail")

    def to_iso(yymmdd, century_threshold=50):
        # YY 00-50 → 20YY, YY 51-99 → 19YY. Covers DOBs back to 1951 and
        # expiries out to 2050, which is enough for a residence card.
        try:
            yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
        except ValueError:
            return ""
        yyyy = 2000 + yy if yy <= century_threshold else 1900 + yy
        return f"{yyyy:04d}-{mm:02d}-{dd:02d}"

    name_raw = l3.rstrip("<")
    if "<<" in name_raw:
        surname, given = name_raw.split("<<", 1)
        name = f"{canon(surname)} {canon(given.replace('<', ' '))}".strip()
    else:
        name = canon(name_raw.replace("<", " "))

    doc_clean = doc_num.rstrip("<")
    id_no = f"{doc_clean[:4]}-{doc_clean[4:8]}" if len(doc_clean) >= 8 else doc_clean

    fields = {
        "name":           name,
        "date_of_birth":  to_iso(dob_raw),
        "sex":            sex,
        "nationality":    canon(nat),
        "date_of_expiry": to_iso(exp_raw),
        "id_no":          id_no,
    }
    return len(errs) == 0, fields, errs


def cross_field(accepted):
    """Cross-field ordering rules. Cheap, deterministic, no oracle needed."""
    errs = []
    def parse(s):
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(s) if s else "")
        return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None

    dob = parse(accepted.get("date_of_birth"))
    iss = parse(accepted.get("date_of_issue"))
    exp = parse(accepted.get("date_of_expiry"))
    if iss and exp and iss > exp:
        errs.append("issue date after expiry")
    if dob and iss and dob > iss:
        errs.append("birth date after issue")
    return errs
