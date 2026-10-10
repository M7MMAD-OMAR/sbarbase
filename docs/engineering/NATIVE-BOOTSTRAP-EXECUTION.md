# Original PostgreSQL bootstrap execution

The local bootstrap prerequisite requires an actual passing original execution
receipt and verified owned cleanup. Source checks, configuration preservation and
image admission do not establish successful PostgreSQL startup, SDK behavior,
recovery or production acceptance.

## Baked source permissions

The source verifier copies the public repository into `/opt/sbarbase`, owned by
its nonroot verifier user. Some historical evidence files have owner-only read
permissions. The identity inspector runs as root with all capabilities dropped;
root cannot read another owner's private files without a bypass capability.

`deploy/verify/Dockerfile.identity` now assigns the baked public source tree to
root while building the identity inspector. It preserves file modes and contents.
Host source permissions, the nonroot offline verifier and production service
images are unaffected. Runtime capabilities remain dropped. This permits the
inspector to compute the complete source digest without skipping evidence files
or granting a file access bypass capability.

## Observed local attempts on 2026-10-10

Both attempts used the public pinned PostgreSQL image
`sha256:b3bfedb107413abb3b8cb0d0874b0414a1dceb3d55bc0c778de6ad22d1f7dc86`,
unique owned fixture names and finite execution/resource limits. The outer check
limited elapsed time to 840 seconds and inherited a 4 MiB file size limit.

1. The first invocation failed while reading a baked historical evidence file,
   before native initialization. Original exit 1 and cleanup receipts are retained.
2. After the ownership repair, the actual inspector obtained source digest
   `8ee499627a863b0b1e1dd7e0e2d45f1ae2561a8bb04652cdb1ae357a298db0b7`
   and completed configuration preservation. Docker then refused creation of the
   isolated network because its predefined address pools were fully subnetted.
   PostgreSQL never started. Original exit 1 remains a failure.

Two independent reviewers verified the second attempt's command artifacts and
exact owned resource absence after cleanup. No unrelated network was removed.
The machine-specific raw receipts are retained under
`/tmp/sbarbase-native-bootstrap-20261010/native/`; these paths are local evidence,
not dependencies required by a public installation.

## Required continuation

The original audit run is unaccepted. The source repair and subsequent execution
belong to the separate repair-mode checkpoint at
`/tmp/sbarbase-native-bootstrap-repair-20261010/run`.

The bootstrap and configured-effects wrapper now admits an explicit `/28` from
the dedicated `10.240.0.0/16` fixture pool. It reads all IPv4 host route tables and
each current Docker network's IPAM configuration, rejects overlaps and ambiguous
inventories, and verifies that the network ID inventory and route observations
remain stable across the capture. It preserves bounded raw observations and their
digests. Unknown IPAM drivers and multipath route observations are refused.

The helper bounds observation to 15 seconds, each command to 5 seconds, stdout to
1 MiB, stderr to 16 KiB, inventory to 256 networks and 4,096 routes, and selection
to 4,096 candidates. The inspector requires the fixture-bound receipt to be no
older than 60 seconds at network creation and verifies the created internal
bridge's owner, subnet and empty consumer inventory before starting the database.
Source tests cover collision, exhausted pool, stale or malformed receipts, route
and network drift, diagnostic and capture refusal. They are not startup proof.

The host snapshot cannot lock unrelated host route changes. Docker rejects a
concurrent conflicting Docker subnet rather than retrying or deleting another
network. No daemon-wide address pool setting is changed and no networks are
pruned. The receipt is a local fixture transcript, not an authenticated grant.
Only an actual original bootstrap rerun with fresh independent reviews can close
this prerequisite. [Docker network creation documentation](https://docs.docker.com/reference/cli/docker/network/create/)
describes explicit subnets, overlap refusal and internal networks.

SB-13a, the 105 capability acceptance rows and release acceptance remain open.
