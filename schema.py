"""Field schema. Single source of truth for detector classes, validators, and
Merkle leaf order. The Merkle order is the declaration order of FIELDS; do not
reorder without invalidating every previously issued credential's proof."""

from dataclasses import dataclass


CARD_W, CARD_H = 900, 570
MRZ_BOX = (40, 470, 820, 80)
MRZ_CLASS = 10
CLASS_NAMES_WITH_MRZ = [
    "name", "date_of_birth", "sex", "nationality", "address", "status",
    "period_of_stay", "id_no", "date_of_issue", "date_of_expiry", "mrz",
]


@dataclass
class Field:
    name: str
    label: str
    validator: str
    in_mrz: bool
    box: tuple = (0, 0, 0, 0)
    cls: int = 0
    multiline: bool = False


FIELDS = [
    Field("name",           "CUSTOMER NAME",    "text",     True,  (300, 120, 560, 40),  0),
    Field("date_of_birth",  "DATE OF BIRTH",    "date",     True,  (300, 190, 240, 40),  1),
    Field("sex",            "GENDER",           "enum:M,F", True,  (580, 190,  60, 40),  2),
    Field("nationality",    "NATIONALITY",      "text",     True,  (660, 190, 200, 40),  3),
    Field("address",        "REGISTERED ADDRESS","text",    False, (300, 260, 560, 80),  4, True),
    Field("status",         "KYC CATEGORY",     "text",     False, (300, 360, 300, 40),  5),
    Field("id_no",          "CUSTOMER REF",     "id_regex", True,  (620, 360, 240, 40),  6),
    Field("period_of_stay", "VALIDITY PERIOD",  "text",     False, (300, 410, 240, 40),  7),
    Field("date_of_issue",  "KYC ISSUED",       "date",     False, (560, 410, 140, 30),  8),
    Field("date_of_expiry", "KYC VALID TILL",   "date",     True,  (720, 410, 140, 30),  9),
]

FIELD_BY_NAME = {f.name: f for f in FIELDS}
