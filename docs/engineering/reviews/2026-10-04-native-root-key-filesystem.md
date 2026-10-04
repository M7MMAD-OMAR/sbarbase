# Native root key publication and metadata guard

Recorded 2026-10-04. Accepted scope: one isolated local Linux amd64 filesystem fixture, private key publication and two fresh read-only guards. PostgreSQL 17.11 startup, provider wiring, warm server restart, Vault decryption continuity, full recovery and production readiness remain unaccepted. The original Supabase image lock is unchanged.

## Observed result

Independent saved-public-data review accepted 889 checks for packet `942cae11bfd94025acfb84976cb80e86`: 48 commands, 96 complete streams, 775330 retained stream bytes, 1193211 evidence bytes and 1.998531393 seconds. Four helpers used native UID 100/GID 101, observed supplementary group 101 and effective group 101. Each had a read-only root, network none, 64 MiB memory and swap limit, .25 CPU, 16 PIDs, dropped capabilities, no-new-privileges, disabled healthcheck, no published ports and restart disabled. Only the exact owned local configuration volume was mounted, with copy-up disabled.

The baseline helper admitted the previously retained 33-object public tree and same-volume metadata. The provision helper generated one private key through [`lab/native_root_key.py`](../../../lab/native_root_key.py). Two different fresh read-only helpers verified that key against its original metadata witness. All 20 public files, 18544 bytes and full new nanosecond metadata remained unchanged; the 12 untouched subdirectories also remained unchanged. Root directory size and times changed only during key publication, while its identity and ownership stayed fixed. The closed final tree contained 34 objects and no pending artifact.

Only metadata and fixed public checks left the fixture. The key was a regular native-owned 0600 file, 64 bytes and one link, with unchanged inode/device and nanosecond times across the guards. Decimal mode 384 denotes 0600. Key bytes and key-derived fingerprints were neither emitted nor read on the host. The accepted module enforces exactly 64 lowercase hexadecimal characters internally with a bounded read. This does not establish that a different valid key can decrypt old Vault data.

All four full CIDs and four exact names were independently absent after cleanup. A filtered consumer query returned empty and the original volume owner/local driver/empty options were renewed. The retained configuration volume now contains the key. Its older key-absent baseline handoff remains historical; future work must admit the current witness and must not rekey or consume the old cold state.

## Exact helper source and build provenance

The helper image was a fresh public-source build from the unchanged immutable verification Dockerfile, admitted by immutable daemon ID `sha256:941cf90d7aec20ec1f6c720cdca552705dc96d77adfc94f8a8809bbfcc12237c`. It supplies Python for this filesystem experiment; no Python availability or runtime compatibility is assumed in the candidate PostgreSQL image. Before provision, fixed public module/test copies of 9107/14681 bytes from the baseline CID matched the independently accepted frozen-v2 source exactly. Both native copy diagnostics were empty.

A distinct read-only completion packet proved the built image's immutable identity, exact explicit tag, image owner, Linux amd64 platform, public User/Entrypoint and null Volumes projection with three inspections, 870 bytes and .111889781 seconds. It did not rewrite or adopt the original failed build receipt. The original native Docker build command had succeeded; its wrapper had stopped before postconditions because a public RUN-step echo included `APT::Update::Error-Mode=any`. The independent completion review classified only that full frozen echo, renewed all missing postconditions and preserved the original FAIL. Runtime continues to reject the old failed receipt.

## Preserved refusals and coverage limits

The first design was refused for accepting a failed build receipt, insufficient host metadata comparison and assuming supplementary groups always include the primary GID. The corrected scope independently rejects all three counterexamples. A first read-only tag preflight refused implicit `:latest`; an explicit unique `:proof` tag was reviewed separately. A missing optional image-field selector was corrected before another native operation. The successful build's source-echo refusal and its original image retention uncertainty remain recorded. Two later auditor expectations were corrected transparently without changing or rerunning the native product trial.

Raw helpers and packets are ignored local artifacts, not distributed evidence or a supported clean-clone acceptance command:

- Native trial: `.lab/native-root-key-runtime-v4-build-20261004/evidence-native-key-942cae11bfd94025acfb84976cb80e86/receipt.json`; review `.lab/native-root-key-runtime-actual-critic-20261004/review.json`.
- Distinct build completion: `.lab/native-key-build-completion-build-20261004/completion-3d78d7f7bfde42e3842cad2c245a7063/receipt.json`; review `.lab/native-key-build-completion-actual-critic-20261004/review.json`.
- Preserved failed designs/builds: `.lab/native-root-key-runtime-design-critic-20261004`, `.lab/native-root-key-first-build-failure-critic-20261004`, `.lab/native-root-key-runtime-v3-design-critic-20261004/optional-volumes-refusal.json` and `.lab/native-root-key-second-build-refusal-critic-20261004`.

The same independent nonbuilder reviewed separate design and actual scopes because new agent thread creation was unavailable. No fresh reviewer identity is claimed. Fsync calls succeeded, but powerloss durability was not tested. No candidate provider, password, SQL or PostgreSQL server ran. Before every future launch, admit the exact current volume and key witness. Bounded private server diagnostics, effective secretsafe settings before sensitive initialization, old encrypted Vault sentinel continuity and coherent physical recovery remain required.

Published source `3b2e32dfcebc71ef1911f1eb964bbf77b8352ed7` passed [CI 37179850916](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37179850916), with the conditional empty-host check skipped, and build-only [Website 37179873086](https://github.com/M7MMAD-OMAR/sbarbase/actions/runs/37179873086). Those results belong to that source revision. This native fragment does not grant release, deployment, security-profile or production acceptance.
