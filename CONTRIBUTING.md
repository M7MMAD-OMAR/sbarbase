[العربية](CONTRIBUTING.ar.md)

# Contributing

Sbarbase is in development. Its value is operational safety: many Supabase projects on one server that you can back up, restore and upgrade without fear. A change is finished when it is tested at the level its risk needs, the evidence is recorded, and the docs say what it does and what it does not do.

## The workflow

Every change goes through the same five steps. Small changes pass through them quickly; changes to the runtime take longer, on purpose.

1. **Pick work from the roadmap.** [docs/engineering/plans/2026-09-23-roadmap.md](docs/engineering/plans/2026-09-23-roadmap.md) orders the work by what a user needs first. If your change is not on it, open an issue and say which user problem it solves.
2. **Change and unit test.** Keep the change to one concern. Run the suites that do not start containers:

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

   A live run writes a JSON file under `docs/evidence/`. Commit it: evidence is how a later reader knows what was true and when.
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
