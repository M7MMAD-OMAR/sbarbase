# Verification of published commit 976250b

All four jobs in [workflow 37611426068](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37611426068) succeeded for exact commit `976250b1625e86066e198ce6f1bf2c7424fd82c3`: checks, Python floor, Docker installation, and empty host acceptance.

The fresh downloaded artifact manifests bound every report to this run and head. Each report had a successful step outcome, a current changed content digest, and a passing schema result. The Docker artifact contained 13 reports with 254 assertions. The empty host artifact contained six reports with 58 unique assertions; identical rehearsal and latest acceptance copies were counted once. Exact public artifact identifiers, archive sizes, and SHA256 values are in `summary.json`. Raw archives and full native logs were retained privately.

The supervisor JSON is a pre-restore snapshot and correctly remains inactive. The separately downloaded native final job log shows the supervised installation being restored, `sbarbase.service` active again with its console answering, and server acceptance passing in that order. `empty-host-final-unit-observation.json` publishes only these fixed markers and the private full-log digest, without adding them to the assertion count. The first log collection was refused by the CLI because the log contained terminal escape sequences. The retry retained the bytes only in a private file and published fixed text markers.

This evidence validates the published CI run and its bounded installation scenarios. It does not establish a physical OS reboot, recovery of every installation material onto a fresh isolated guest, or continuing Cron behavior across recovery and restart. Production acceptance remains open.
