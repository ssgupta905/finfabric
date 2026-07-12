"""Salted Merkle commitments, selective disclosure, and epoch batching.

Two departures from BDIMS, both load-bearing:

1. Leaves commit to canonical FIELD VALUES, not image segments. An image hash
   proves bytes, not identity: re-encode the JPEG and the proof dies, while a
   forger who keeps the bytes and swaps the meaning is unaffected.

2. Every leaf carries a fresh 128-bit salt. Without one, `sha256("sex=M")` is a
   two-guess brute force, so an undisclosed field is not actually hidden — and
   the same field hash presented to two verifiers lets them link the holder.
   Salts are given to the holder and never published.

Batching: each credential's root becomes a leaf of an epoch tree, and only the
epoch root is anchored. One transaction per epoch, regardless of how many
credentials were issued in it. This is what makes the chain cost constant.
"""

import hashlib
import json
import os
from dataclasses import dataclass, field as dc_field
from typing import List


def H(*parts: bytes) -> bytes:
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return h.digest()


def leaf_hash(name: str, value: str, salt: bytes) -> bytes:
    # Domain-separated, length-prefixed: prevents a second-preimage where a
    # crafted field name absorbs part of the value.
    return H(b"\x00leaf", len(name).to_bytes(2, "big"), name.encode(),
             len(value).to_bytes(2, "big"), value.encode(), salt)


def node_hash(a: bytes, b: bytes) -> bytes:
    return H(b"\x01node", a, b)


def build_tree(leaves: List[bytes]):
    """Returns (root, levels). Odd nodes are promoted, not duplicated —
    duplicating the last node is the classic CVE-2012-2459 malleability bug."""
    if not leaves:
        raise ValueError("no leaves")
    levels = [list(leaves)]
    while len(levels[-1]) > 1:
        cur = levels[-1]
        nxt = [node_hash(cur[i], cur[i + 1]) for i in range(0, len(cur) - 1, 2)]
        if len(cur) % 2:
            nxt.append(cur[-1])
        levels.append(nxt)
    return levels[-1][0], levels


def make_proof(levels, index: int):
    """Sibling path as [(hash, is_left), ...]."""
    path = []
    for lvl in levels[:-1]:
        if index ^ 1 < len(lvl):
            sib = lvl[index ^ 1]
            path.append((sib, index % 2 == 1))
        index //= 2
    return path


def verify_proof(leaf: bytes, path, root: bytes) -> bool:
    h = leaf
    for sib, sib_is_left in path:
        h = node_hash(sib, h) if sib_is_left else node_hash(h, sib)
    return h == root


@dataclass
class Credential:
    """What the holder stores. The salts never leave the wallet except in the
    specific disclosures the holder chooses to make."""
    subject_did: str
    issuer_did: str
    order: List[str]
    values: dict
    salts: dict = dc_field(repr=False, default_factory=dict)
    root: bytes = b""
    epoch_id: int = -1
    epoch_proof: list = dc_field(default_factory=list)

    def disclose(self, names: List[str]):
        """Produce a presentation revealing exactly `names` and nothing else."""
        leaves = [leaf_hash(n, self.values[n], self.salts[n]) for n in self.order]
        _, levels = build_tree(leaves)
        out = []
        for n in names:
            i = self.order.index(n)
            out.append({
                "name": n,
                "value": self.values[n],
                "salt": self.salts[n].hex(),
                "path": [(h.hex(), l) for h, l in make_proof(levels, i)],
            })
        return {
            "subject": self.subject_did,
            "issuer": self.issuer_did,
            "credential_root": self.root.hex(),
            "epoch_id": self.epoch_id,
            "epoch_proof": [(h.hex(), l) for h, l in self.epoch_proof],
            "disclosures": out,
        }


def issue(subject_did, issuer_did, values: dict, order: List[str]) -> Credential:
    salts = {n: os.urandom(16) for n in order}
    leaves = [leaf_hash(n, values[n], salts[n]) for n in order]
    root, _ = build_tree(leaves)
    return Credential(subject_did, issuer_did, order, dict(values), salts, root)


def anchor_epoch(creds: List[Credential], epoch_id: int) -> bytes:
    """Fold a batch of credential roots into one epoch root. This is the only
    value that ever touches the chain."""
    roots = [c.root for c in creds]
    epoch_root, levels = build_tree(roots)
    for i, c in enumerate(creds):
        c.epoch_id = epoch_id
        c.epoch_proof = make_proof(levels, i)
    return epoch_root


def verify_presentation(pres: dict, epoch_root_onchain: bytes, revoked: bool = False):
    """What a relying party runs. Two Merkle checks and a revocation bit —
    no chain writes, no issuer callback, no personal data anywhere."""
    if revoked:
        return False, {}, "credential is revoked"

    cred_root = bytes.fromhex(pres["credential_root"])

    # 1. the credential root is inside the anchored epoch root
    ep = [(bytes.fromhex(h), l) for h, l in pres["epoch_proof"]]
    if not verify_proof(cred_root, ep, epoch_root_onchain):
        return False, {}, "credential root not in anchored epoch"

    # 2. each disclosed field is inside the credential root
    revealed = {}
    for d in pres["disclosures"]:
        lf = leaf_hash(d["name"], d["value"], bytes.fromhex(d["salt"]))
        path = [(bytes.fromhex(h), l) for h, l in d["path"]]
        if not verify_proof(lf, path, cred_root):
            return False, {}, f"field '{d['name']}' failed its Merkle proof"
        revealed[d["name"]] = d["value"]

    return True, revealed, "ok"
