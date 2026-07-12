"""Bridge from merkle.py to the on-chain registries.

Two modes, chosen by the USE_LIVE_CHAIN env var:

  live      — sends real transactions to Base Sepolia via web3.py
  fixture   — returns pre-computed values with a plausible tx hash and gas
              number, so the UI works without any chain setup

Fixture mode is what makes the demo runnable the moment you clone the repo.
Flip USE_LIVE_CHAIN=1 (after `forge script Deploy`) to switch to real chain."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "fixtures"

ROOT_ABI = [
    {"type": "function", "name": "anchor", "stateMutability": "nonpayable",
     "inputs": [{"name": "epochId", "type": "uint256"},
                {"name": "root", "type": "bytes32"},
                {"name": "credentialCount", "type": "uint32"}], "outputs": []},
    {"type": "function", "name": "rootOf", "stateMutability": "view",
     "inputs": [{"name": "epochId", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bytes32"}]},
    {"type": "function", "name": "isIssuer", "stateMutability": "view",
     "inputs": [{"name": "", "type": "address"}],
     "outputs": [{"name": "", "type": "bool"}]},
]

STATUS_ABI = [
    {"type": "function", "name": "publish", "stateMutability": "nonpayable",
     "inputs": [{"name": "uri", "type": "string"},
                {"name": "listHash", "type": "bytes32"},
                {"name": "version", "type": "uint64"}], "outputs": []},
    {"type": "function", "name": "statusOf", "stateMutability": "view",
     "inputs": [{"name": "", "type": "address"}],
     "outputs": [{"name": "uri", "type": "string"},
                 {"name": "listHash", "type": "bytes32"},
                 {"name": "updatedAt", "type": "uint64"},
                 {"name": "version", "type": "uint64"}]},
]


def _load_env():
    env_path = REPO / ".env"
    env: dict[str, str] = dict(os.environ)
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    return env


def _live() -> bool:
    return _load_env().get("USE_LIVE_CHAIN", "0") == "1"


def _basescan(tx_hash: str) -> str:
    return f"https://sepolia.basescan.org/tx/{tx_hash}"


def _address_url(addr: str) -> str:
    return f"https://sepolia.basescan.org/address/{addr}"


def _w3_and_acct():
    from web3 import Web3
    env = _load_env()
    w3 = Web3(Web3.HTTPProvider(env["BASE_SEPOLIA_RPC"]))
    acct = w3.eth.account.from_key(env["ISSUER_PRIVATE_KEY"])
    return w3, acct, env


def anchor_epoch_onchain(epoch_id: int, epoch_root: bytes, count: int) -> dict:
    """Return {tx_hash, gas_used, gas_price_wei, block_number, basescan_url,
    epoch_root_hex, epoch_id, credential_count, mode}."""
    root_hex = "0x" + epoch_root.hex()
    if not _live():
        # Deterministic tx hash so the UI can render a plausible receipt.
        fake_tx = "0x" + ("0" * 24) + epoch_root.hex()[:40]
        return {
            "tx_hash": fake_tx,
            "gas_used": 46_212,
            "gas_price_wei": 1_500_000,   # ~0.0015 gwei, typical Base Sepolia
            "block_number": 12_345_678,
            "basescan_url": _basescan(fake_tx),
            "epoch_root_hex": root_hex,
            "epoch_id": epoch_id,
            "credential_count": count,
            "mode": "fixture",
            "cost_usd": 46_212 * 1_500_000 * 1e-18 * 3800,  # gas * price * eth_usd
        }

    w3, acct, env = _w3_and_acct()
    root_addr = env["ROOT_REGISTRY_ADDRESS"]
    c = w3.eth.contract(address=root_addr, abi=ROOT_ABI)
    fn = c.functions.anchor(epoch_id, epoch_root, count)
    tx = fn.build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": 84532,
        "gas": 120_000,
        "maxFeePerGas": w3.eth.gas_price * 2,
        "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    rcpt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    return {
        "tx_hash": rcpt.transactionHash.hex(),
        "gas_used": rcpt.gasUsed,
        "gas_price_wei": rcpt.effectiveGasPrice,
        "block_number": rcpt.blockNumber,
        "basescan_url": _basescan(rcpt.transactionHash.hex()),
        "epoch_root_hex": root_hex,
        "epoch_id": epoch_id,
        "credential_count": count,
        "mode": "live",
        "cost_usd": rcpt.gasUsed * rcpt.effectiveGasPrice * 1e-18 * 3800,
    }


def read_root_onchain(epoch_id: int) -> Optional[bytes]:
    """Fetch a previously anchored root. Returns None if not found or fixture."""
    if not _live():
        cached = FIXTURES / "pre_anchored.json"
        if cached.exists():
            data = json.loads(cached.read_text())
            if data.get("epoch_id") == epoch_id:
                return bytes.fromhex(data["epoch_root_hex"].removeprefix("0x"))
        return None
    w3, _, env = _w3_and_acct()
    c = w3.eth.contract(address=env["ROOT_REGISTRY_ADDRESS"], abi=ROOT_ABI)
    val = c.functions.rootOf(epoch_id).call()
    return bytes(val) if val and val != b"\x00" * 32 else None


def publish_status(uri: str, list_hash: bytes, version: int) -> dict:
    if not _live():
        fake_tx = "0x" + ("f" * 24) + list_hash.hex()[:40]
        return {
            "tx_hash": fake_tx,
            "basescan_url": _basescan(fake_tx),
            "uri": uri,
            "list_hash_hex": "0x" + list_hash.hex(),
            "version": version,
            "mode": "fixture",
            "gas_used": 51_320,
        }
    w3, acct, env = _w3_and_acct()
    c = w3.eth.contract(address=env["STATUS_REGISTRY_ADDRESS"], abi=STATUS_ABI)
    fn = c.functions.publish(uri, list_hash, version)
    tx = fn.build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": 84532,
        "gas": 150_000,
        "maxFeePerGas": w3.eth.gas_price * 2,
        "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    rcpt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    return {
        "tx_hash": rcpt.transactionHash.hex(),
        "basescan_url": _basescan(rcpt.transactionHash.hex()),
        "uri": uri,
        "list_hash_hex": "0x" + list_hash.hex(),
        "version": version,
        "mode": "live",
        "gas_used": rcpt.gasUsed,
    }


def ping() -> None:
    """Sanity: is the RPC reachable and is our key whitelisted?"""
    if not _live():
        print("fixture mode — no chain")
        return
    w3, acct, env = _w3_and_acct()
    root = w3.eth.contract(address=env["ROOT_REGISTRY_ADDRESS"], abi=ROOT_ABI)
    ok = root.functions.isIssuer(acct.address).call()
    print(f"chain {'OK' if w3.is_connected() else 'DOWN'} — issuer {acct.address} "
          f"{'whitelisted' if ok else 'NOT whitelisted'} on RootRegistry")
