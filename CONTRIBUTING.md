[العربية](CONTRIBUTING.ar.md)

# Contributing

Sbarbase is in development. Its value is operational safety: tested multi-project Supabase operation, backup, restore and upgrades on one server. A change is finished when it is tested at the level its risk needs, the evidence is recorded, and the docs say what it does and what it does not do.

## The workflow

Every change goes through the same five steps. Small changes pass through them quickly; changes to the runtime take longer, on purpose.

1. **Pick work from the roadmap.** The [October product plan](docs/engineering/plans/2026-10-03-product-and-portability-plan.md) orders current work; the [execution method](docs/engineering/plans/2026-10-03-gauntlet-execution-method.md) defines acceptance. September milestones remain historical. If your change is not on it, open an issue and say which user problem it solves.
2. **Change and unit test.** Keep the change to one concern. The primary source gate is `sh deploy/verify/run.sh`, a bounded disposable Docker verifier with no execution-time network. See [its scope](deploy/verify/README.md). For a configured development host, the underlying suites are:

   ```bash
   bun install --frozen-lockfile
   bun test
   bun run typecheck:ui
   DOCKER_HOST=unix:///var/run/docker.sock /usr/bin/python3 -m unittest discover -s lab -p 'test_*.py'
   ```

   A defect gets a test that fails before the fix. A new refusal gets a test for the refusal and one for the case it must still admit.
3. **Prove it live when it touches the runtime.** Anything under `lab/` that starts, stops or configures containers, anything in `deploy/`, and any change to admission, provisioning, fencing or recovery needs a live run:
   - on your machine, the bounded probes described in [lab/README.md](lab/README.md) (read it first: some probes consume retained fixtures);
   - for install and upgrade paths, the empty-server rehearsal in a local VM: `lab/vm-rehearsal.sh --image <Fedora 44 Cloud qcow2>`. It clones the current commit into a fresh VM, runs the one-command acceptance with a first project, reboots and requires the service back.

   Record the declared source identity, fixture, scope, verdict and failures. Historical probes use `docs/evidence/`; October immutable fragments are indexed in the [ledger](docs/engineering/gauntlet-ledger.json), including local packets that are not distributed. Public reproducibility and release acceptance require their own complete evidence; never publish private state or transfer a result to edited source.
4. **Review adversarially.** Runtime changes get a second reviewer, person or agent, whose job is to break the change: crash it half way, run it twice, run it as the wrong user, on a partition instead of a disk, with the port taken. Record what they found and what was fixed. Several defects in this repository were found only this way or only by running the documented command on an empty machine.
5. **Update the docs in the same change.** The page that explains the area ([docs/README.md](docs/README.md) maps them), the numbers in [docs/reference/status.md](docs/reference/status.md) and the readiness row in [docs/reference/deployment-readiness.md](docs/reference/deployment-readiness.md) when a claim changes. The Arabic page (`.ar.md`) is updated in the same change; `lab/test_docs_bilingual.py` checks the pairs. `lab/test_docs_links.py` fails on a broken link or a long dash; `lab/test_doc_references.py` fails when a runbook names a script or evidence file that does not exist.

## Rules that are not negotiable

- **Never weaken a safety refusal to get past it.** Do not delete receipts, journals, generation pins, tombstones or revocations, and do not recreate a retained database container. If a refusal is wrong, change the rule with a test and a written reason, as the CPU admission rule was changed.
- **No secrets anywhere they can travel.** `.secrets/` and `.lab/` are ignored and never read into logs, evidence, issues or commits. Passwords are read from 0600 files or stdin, never from arguments.
- **Honest claims only.** Do not claim production readiness, full security, automatic recovery or a fixed number of projects per server. Say what was run, where, and what it does not prove.
- **Original upstream services.** Supabase components run as pinned upstream images, changed only through [the update policy](docs/engineering/UPSTREAM-UPDATE-POLICY.md). Forks and patches of upstream services are out of scope.

## Style

- bun for everything JavaScript (`bun install`, `bun run`, `bun test`), never npm or npx; `/usr/bin/python3` for the Python runtime.
- English in code, comments and commit messages. Reader-facing documentation is in English and Arabic; see [docs/engineering/ARABIC-DOCS.md](docs/engineering/ARABIC-DOCS.md). No em or en dashes anywhere; use commas, colons or two sentences.
- Conventional commits: `fix(install): ...`, `feat(console): ...`, `docs: ...`. Stage only the paths of your change.
- Match the surrounding code: its naming, its comment density, its terseness.

## Security issues

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
