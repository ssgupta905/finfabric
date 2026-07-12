# Deploy to Base Sepolia

The demo runs in **fixture mode** without any of this — the numbers and Merkle
roots are real, just not anchored on a real chain. Do the steps below only when
you want live on-chain transactions during the demo.

## 1. Install Foundry

```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
```

## 2. Get Base Sepolia ETH

Any of these free faucets work; you need ~0.05 ETH for many demo runs:

- https://www.alchemy.com/faucets/base-sepolia
- https://portal.cdp.coinbase.com/products/faucet
- https://learnweb3.io/faucets/base_sepolia

Send it to the address for the private key you'll use as the issuer.

## 3. Configure

```bash
cp .env.example .env
# then edit .env:
#   ISSUER_PRIVATE_KEY = your funded Base Sepolia key
#   BASE_SEPOLIA_RPC   = https://sepolia.base.org  (or Alchemy)
```

## 4. Deploy

```bash
cd contracts
forge install foundry-rs/forge-std --no-commit
source ../.env
forge script script/Deploy.s.sol \
  --rpc-url $BASE_SEPOLIA_RPC \
  --private-key $ISSUER_PRIVATE_KEY \
  --broadcast
```

Copy the two printed addresses into `.env` as `ROOT_REGISTRY_ADDRESS` and
`STATUS_REGISTRY_ADDRESS`, then set `USE_LIVE_CHAIN=1`.

## 5. Optional: verify source on Basescan

```bash
forge verify-contract $ROOT_REGISTRY_ADDRESS RootRegistry \
  --chain base-sepolia --etherscan-api-key $BASESCAN_API_KEY
forge verify-contract $STATUS_REGISTRY_ADDRESS StatusRegistry \
  --chain base-sepolia --etherscan-api-key $BASESCAN_API_KEY
```

Verified source on Basescan is a big credibility win during the demo — judges
can read your contracts in-browser.

## 6. Sanity check

```bash
cd ..
python -c "from issuer.anchor_client import ping; ping()"
```

Should print `chain OK — issuer 0x... whitelisted on both registries`.
