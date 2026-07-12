// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import "../src/RootRegistry.sol";
import "../src/StatusRegistry.sol";

/// Deploys both registries and whitelists the deployer as an issuer on each.
/// Run: forge script script/Deploy.s.sol --rpc-url $BASE_SEPOLIA_RPC \
///                                       --private-key $PK --broadcast
contract Deploy is Script {
    function run() external {
        vm.startBroadcast();

        RootRegistry root = new RootRegistry();
        StatusRegistry status = new StatusRegistry();

        root.setIssuer(msg.sender, true);
        status.setIssuer(msg.sender, true);

        console2.log("RootRegistry:", address(root));
        console2.log("StatusRegistry:", address(status));

        vm.stopBroadcast();
    }
}
