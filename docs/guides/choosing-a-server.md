[العربية](choosing-a-server.ar.md)

# Choosing a server

No external server is needed for the current work: the selected server is an isolated Linux environment on the user's computer. Ubuntu 26.04 empty-host installation and the shipped system service passed [CI 37600158231](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37600158231) at `e08ed3b`. The sizing and provider-price examples below retain their September 23 scope; current production, reboot and full recovery acceptance remain open. See [current status](../reference/status.md).

## What the software asks for

| | Minimum that is useful | Recommended |
|---|---|---|
| CPU | 3 cores, x86-64 | 4 cores |
| Memory | 8 GB | 8 GB; 16 GB if you also want room to rehearse a restore on the same machine |
| Disk | 12 GiB free; the pinned images take about 2.4 GB | 80 GB or more, NVMe, to hold data and local backups |
| System | `/usr/bin/python3` 3.12 or newer with `cryptography`, Docker, systemd | Ubuntu 26.04 (current CI install and system service); Fedora 44 (historical rehearsal); Ubuntu 24.04 and Debian 13 (no current full install acceptance) |

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
- **Python 3.12 is the floor.** Ubuntu 24.04 ships 3.12 and Debian 13 ships 3.13. The Python unit tests pass on both with their own `python3` and `python3-cryptography` packages, apart from checks that need a real host (a block device, systemd). These historical floor results do not establish a current full install on Ubuntu 24.04 or Debian 13. Current clean-host CI covers Ubuntu 26.04 with Python 3.14; its separate floor job passes 2102 tests on Python 3.12.

The four-environment guard (for example two clients with production and staging each) does not come from the hardware. Lifting it waits for measurements on a real server; see the roadmap.

The preflight prints the exact figure for your server: `/usr/bin/python3 lab/install_server.py check`.

## Historical paid examples (x86-64)

Prices seen on 2026-09-23, before tax unless stated. These are archived examples, not current quotations. The table lists the 8 core and 16 to 24 GB plans that were checked in detail, as the step up.

| Provider and plan | vCPU / RAM / disk | Price | Notes |
|---|---|---|---|
| Hetzner CX43 | 8 / 16 GB / 160 GB | EUR 15.99 a month | hourly billing, no minimum term; smaller CX plans exist for a 4 core start ([price change notice](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/)) |
| Contabo Cloud VPS 8 | 8 / 24 GB / 300 GB SSD | EUR 14.00 a month incl. VAT | that price needs a 24 month term ([contabo.com/en/vps](https://contabo.com/en/vps/)) |
| OVHcloud VPS-4 | 8 / 24 GB / 200 GB NVMe | from USD 23.37 a month | 12 month commitment ([ovhcloud.com/en/vps](https://www.ovhcloud.com/en/vps/)) |
| Netcup RS 2000 | 8 dedicated / 16 GB / 256 GB NVMe | EUR 40.70 a month | dedicated cores, useful for timing measurements ([netcup.com](https://www.netcup.com/en/server/root-server)) |

Choose a data centre near your applications' users, and confirm the provider offers Fedora 44 or Ubuntu 26.04 images, or lets you upload one.

## Free options, honestly

- **A virtual machine on your own computer.** This is the selected local-server route. The earlier Fedora cloud-image rehearsal is historical. The new full-recovery guest driver still needs operational acceptance, including reboot and lost-source restore; see [current status](../reference/status.md). Local evaluation does not require buying a server.
- **GitHub Actions runners.** The standard public Linux runner is documented as 4 CPU, 16 GB RAM and 14 GB storage. [GitHub runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners). The `empty-host-acceptance` job now runs on main pushes and explicit manual acceptance requests. Current Ubuntu 26.04 CI proves installation and a live shipped system service within that job. The ephemeral runner does not prove reboot persistence, public HTTPS or an ongoing production deployment. Check measured headroom in each run; advertised storage is not observed free space.
- **Oracle Ampere A1.** Its arm64 architecture is outside the current x86_64 host profile. It is not an accepted deployment route. Check the [official free-tier conditions](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) before evaluating any offer.
- **Other free tiers.** Compare the actual architecture, available memory, Docker support, disk headroom and account terms with the current host preflight before choosing a provider.

## After you buy

Follow [the quickstart](quickstart.md). Keep SSH and ports 80 and 443 open, nothing else; the console and the gateway listen on loopback only, behind the TLS proxy.
