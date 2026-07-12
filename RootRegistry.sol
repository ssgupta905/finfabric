// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title RootRegistry
/// @notice The entire on-chain footprint of the identity system: one 32-byte
///         Merkle root per issuance epoch. No personal data, no ciphertext, no
///         per-user registration, no DID document. A credential's existence is
///         proven by a Merkle path from its root to an epoch root stored here.
///
///         Cost is a function of TIME, not of USERS. One anchor per epoch serves
///         an unbounded number of credentials issued in that epoch. At ~46k gas
///         on an L2 this is fractions of a cent, and hourly epochs cost well
///         under a dollar a month at any scale.
///
///         Nothing here is encrypted, because nothing here is private. That is
///         the point: an encrypted blob on an immutable ledger is a personal
///         data breach with a delay fuse, waiting on the day the key leaks.
contract RootRegistry {
    struct Epoch {
        bytes32 root;
        uint64  anchoredAt;
        uint32  credentialCount; // for auditability only; not trusted by verifiers
    }

    address public admin;
    mapping(address => bool) public isIssuer;
    mapping(uint256 => Epoch) public epochs;
    uint256 public latestEpoch;

    event IssuerSet(address indexed issuer, bool allowed);
    event EpochAnchored(uint256 indexed epochId, bytes32 root, uint32 credentialCount);

    error NotIssuer();
    error NotAdmin();
    error EpochExists();
    error EmptyRoot();

    constructor() {
        admin = msg.sender;
    }

    modifier onlyAdmin() {
        if (msg.sender != admin) revert NotAdmin();
        _;
    }

    function setIssuer(address issuer, bool allowed) external onlyAdmin {
        isIssuer[issuer] = allowed;
        emit IssuerSet(issuer, allowed);
    }

    /// @notice Anchor one epoch. Append-only: an epoch can never be rewritten,
    ///         which is what makes the issuance log evidence rather than a claim.
    ///         An issuer cannot back-date a credential into a sealed epoch.
    function anchor(uint256 epochId, bytes32 root, uint32 credentialCount) external {
        if (!isIssuer[msg.sender]) revert NotIssuer();
        if (root == bytes32(0)) revert EmptyRoot();
        if (epochs[epochId].root != bytes32(0)) revert EpochExists();

        epochs[epochId] = Epoch(root, uint64(block.timestamp), credentialCount);
        if (epochId > latestEpoch) latestEpoch = epochId;

        emit EpochAnchored(epochId, root, credentialCount);
    }

    /// @notice Free for relying parties: a `view` call costs no gas.
    function rootOf(uint256 epochId) external view returns (bytes32) {
        return epochs[epochId].root;
    }
}
