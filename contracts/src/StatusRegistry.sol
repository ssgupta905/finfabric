// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title StatusRegistry
/// @notice Revocation without per-credential transactions.
///
///         BDIMS revokes by writing an on-chain transaction per attribute per
///         service provider. That makes revocation — the operation you most
///         want a user to perform freely, instantly, and without thinking about
///         it — the single most expensive action in the system. A user who must
///         pay gas to withdraw consent has not really been given consent.
///
///         Instead: a StatusList2021 bitstring (one bit per credential index,
///         gzipped, typically a few KB for 100k credentials) is published to
///         IPFS or static hosting. Only its URI and hash are anchored here.
///         Revoking one credential or a hundred thousand costs exactly one
///         transaction, and a relying party checks a bit rather than a ledger.
contract StatusRegistry {
    struct Status {
        string  uri;        // where the bitstring lives (ipfs:// or https://)
        bytes32 listHash;   // keccak of the published bitstring — detects substitution
        uint64  updatedAt;
        uint64  version;
    }

    address public admin;
    mapping(address => bool) public isIssuer;
    mapping(address => Status) public statusOf; // issuer => their status list

    event StatusPublished(address indexed issuer, string uri, bytes32 listHash, uint64 version);

    error NotIssuer();
    error NotAdmin();
    error StaleVersion();

    constructor() {
        admin = msg.sender;
    }

    function setIssuer(address issuer, bool allowed) external {
        if (msg.sender != admin) revert NotAdmin();
        isIssuer[issuer] = allowed;
    }

    /// @notice Publish a new revocation bitstring. Monotonic version prevents a
    ///         compromised or coerced issuer from rolling back to an older list
    ///         in which a revoked credential was still valid.
    function publish(string calldata uri, bytes32 listHash, uint64 version) external {
        if (!isIssuer[msg.sender]) revert NotIssuer();
        if (version <= statusOf[msg.sender].version) revert StaleVersion();

        statusOf[msg.sender] = Status(uri, listHash, uint64(block.timestamp), version);
        emit StatusPublished(msg.sender, uri, listHash, version);
    }
}
