#!/bin/sh
# Runs inside the control-plane container, in the checkout mounted at the same
# path as on the host. Runs the upgrade guard, installs dependencies and pinned
# images, then hands over to the foreground supervisor, which the container
# restart policy keeps alive.
set -eu
cd "${SBARBASE_ROOT:?SBARBASE_ROOT must name the checkout}"
# First, before any code of the version the checkout holds (lab/upgrade_guard.py):
# the copy an upgrade took from the version it left when there is one. While an
# upgrade waits for its health checks it may move the checkout back, so the steps
# below run the previous version. A refusal exits here and the restart policy
# tries again.
if [ -f .lab/upgrades/guard.py ]; then
  /usr/bin/python3 .lab/upgrades/guard.py
else
  /usr/bin/python3 lab/upgrade_guard.py
fi
export SBARBASE_GUARDED=1
bun install --frozen-lockfile
/usr/bin/python3 lab/install_server.py images
exec /usr/bin/python3 lab/dev.py "$@"
