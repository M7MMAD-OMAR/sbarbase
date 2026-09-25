[العربية](choosing-a-server.ar.md)

# Choosing a server

What to buy, or borrow for free, to run Sbarbase. Figures come from the empty-server rehearsal in a local VM on 2026-09-23 and from the preflight's own rules; prices were read from the providers' pages the same day and change often, so check them when you order.

## What the software asks for

| | Minimum that is useful | Recommended |
|---|---|---|
| CPU | 3 cores, x86-64 | 4 cores |
| Memory | 8 GB | 8 GB; 16 GB if you also want room to rehearse a restore on the same machine |
| Disk | 12 GiB free; the pinned images take about 2.4 GB | 80 GB or more, NVMe, to hold data and local backups |
| System | `/usr/bin/python3` 3.12 or newer with `cryptography`, Docker, systemd | Fedora 44 (rehearsed); Ubuntu 26.04 LTS, Ubuntu 24.04 LTS or Debian 13 (not rehearsed yet) |

How many environments fit, by the same rules the preflight, the runtime and the provisioning worker apply (approximate for memory, because it depends on what the operating system itself uses; `lab/install_server.py check` gives the exact figure for your server):

| Server | Environments that fit |
|---|---|
| 2 cores, any memory | none: the installation starts, but no environment can be added |
| 3 cores | up to 4 by CPU |
| 4 cores | up to 8 by CPU |
| 4 GB | none: the installation itself does not fit |
| 6 GB | about 1 |
| 8 GB | about 5 by memory |
| 16 GB | about 21 by memory |

Every server is also held to at most four environments for now by a guard kept from the lab (below), so 3 or 4 cores with 8 GB is where that limit, not the hardware, becomes the ceiling. The `--first-project` step of the install creates one of those environments and a test application user; delete the test user from that environment if you keep it.

Why these numbers:

- **Memory is counted by limits, not use.** An empty installation measured about 190 MiB for its three containers and 130 MiB for the supervisor, but the preflight reserves each container's memory limit plus 2560 MiB for the host, because a container that outgrows a shared host is killed. Each environment's Auth and REST add 256 MiB of limit each.
- **CPU is counted as ceilings.** One core stays with the host and the containers' CPU ceilings may add up to twice the rest, because idle services use almost nothing and a busy one is slowed, not killed. Real contention is caught by the pressure gate.
- **Python 3.12 is the floor.** Ubuntu 24.04 ships 3.12 and Debian 13 ships 3.13. The Python unit tests pass on both with their own `python3` and `python3-cryptography` packages, apart from checks that need a real host (a block device, systemd). Neither has had an install rehearsed end to end yet, so Fedora 44 remains the only rehearsed system.

The four-environment guard (for example two clients with production and staging each) does not come from the hardware. Lifting it waits for measurements on a real server; see the roadmap.

The preflight prints the exact figure for your server: `/usr/bin/python3 lab/install_server.py check`.

## Paid options (x86-64)

Prices seen on 2026-09-23, before tax unless stated. Pick 4 cores and 8 GB for a start; the table lists the 8 core and 16 to 24 GB plans that were checked in detail, as the step up.

| Provider and plan | vCPU / RAM / disk | Price | Notes |
|---|---|---|---|
| Hetzner CX43 | 8 / 16 GB / 160 GB | EUR 15.99 a month | hourly billing, no minimum term; smaller CX plans exist for a 4 core start ([price change notice](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/)) |
| Contabo Cloud VPS 8 | 8 / 24 GB / 300 GB SSD | EUR 14.00 a month incl. VAT | that price needs a 24 month term ([contabo.com/en/vps](https://contabo.com/en/vps/)) |
| OVHcloud VPS-4 | 8 / 24 GB / 200 GB NVMe | from USD 23.37 a month | 12 month commitment ([ovhcloud.com/en/vps](https://www.ovhcloud.com/en/vps/)) |
| Netcup RS 2000 | 8 dedicated / 16 GB / 256 GB NVMe | EUR 40.70 a month | dedicated cores, useful for timing measurements ([netcup.com](https://www.netcup.com/en/server/root-server)) |

Choose a data centre near your applications' users, and confirm the provider offers Fedora 44 or Ubuntu 26.04 images, or lets you upload one.

## Free options, honestly

- **A virtual machine on your own computer.** `lab/vm-rehearsal.sh --image <Fedora 44 Cloud qcow2>` builds a disposable server from a stock cloud image, installs from a clean clone, creates a first project and reboots it. This is how the install was tested before any server existed. It proves the install path, not public networking or a certificate.
- **GitHub Actions runners.** Public repositories get 4 CPU, 16 GB Linux runners, and `ubuntu-26.04` ships Python 3.14. The CI workflow has a manual `empty-host-acceptance` job that runs the one-command acceptance there. The documented disk is 14 GB, which is tight against the 12 GiB the preflight wants; the job prints `df` first. A runner lives for one job, so it proves an install from nothing, not a running service.
- **Oracle Cloud Always Free, Ampere A1.** About 2 OCPU and 12 GB on arm64 ([Oracle's free tier page](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)). With 2 cores the installation would start but no environment could be added, so it is not a usable free server for Sbarbase today. Every pinned image does include linux/arm64, but arm64 has not been rehearsed. Oracle reclaims idle instances and signup needs a payment card for verification.
- **Google Cloud and AWS free tiers** are too small (1 GB machines) or time-limited credits.

## After you buy

Follow [the quickstart](quickstart.md). Keep SSH and ports 80 and 443 open, nothing else; the console and the gateway listen on loopback only, behind the TLS proxy.
