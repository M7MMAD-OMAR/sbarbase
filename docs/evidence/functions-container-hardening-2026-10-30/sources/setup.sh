#!/bin/bash
# Builds the work directory the other scripts use: the function sources, a private directory
# shaped like lab.secure_file's output (directory 700, secrets.json 600, owner = the invoking user),
# and nothing else. WORK defaults to ~/.cache/sbarbase-container-exp.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${WORK:-$HOME/.cache/sbarbase-container-exp}
mkdir -p "$WORK/priv"
cp -r "$HERE/code" "$WORK/"
cp "$HERE"/*.sh "$WORK/"
echo '{"MY_SECRET":"s3cret"}' > "$WORK/priv/secrets.json"
chmod 700 "$WORK/priv"
chmod 600 "$WORK/priv/secrets.json"
chmod -R a+rX "$WORK/code"
stat -c '%u %g %a %n' "$WORK/priv" "$WORK/priv/secrets.json"
