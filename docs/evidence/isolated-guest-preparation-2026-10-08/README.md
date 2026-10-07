Isolated guest preparation checkpoint, 2026-10-08

The CI record was read from GitHub for published commit `1c4449ff4c530029096453d32fc12c2d73de93f1`. All four jobs completed successfully. This record does not rerun or extend those jobs.

The two public Source5 artifacts preserve a candidate disposable guest preparation script and six actual regression fixtures. Their exact sizes and SHA256 are in source-manifest.json. The fixtures ran locally under a finite Root system service with MemoryMax=512M, MemorySwapMax=0, CPUQuota=50%, TasksMax=128, RuntimeMaxSec=60 and LimitNOFILE=8192, with six passes in 0.178 seconds. The [independent source review](independent-review.md) accepts the implemented repair and narrow checkpoint scope; the reviewer did not execute the fixtures.

The script is experimental, fixed Linux amd64 guest preparation and still binds historical published source `1c4449ff` and 1690 files. It is not the production installer, and these workstation experiments do not introduce host helper paths or byte pins as product requirements. Original clocks, process custody and refusals are tested; guest boot, Docker package installation and import, original Supabase service operation, complete recovery and production acceptance are not established.

A separate local preparation build ran with networking disabled using the published source and previously verified public dependency bytes. Its 1690 source files and 6557 portable cache entries were checked. That fixed observation does not accept a general cache provenance factory or Ubuntu guest binary compatibility.
