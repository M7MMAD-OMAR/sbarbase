#!/bin/sh
# Runs inside the control-plane container, in the checkout mounted at the same
# path as on the host. Installs dependencies and pinned images, then hands over
# to the foreground supervisor, which the container restart policy keeps alive.
set -eu
cd "${SBARBASE_ROOT:?SBARBASE_ROOT must name the checkout}"
[ -d node_modules ] || bun install --frozen-lockfile
/usr/bin/python3 lab/install_server.py images
exec /usr/bin/python3 lab/dev.py "$@"
