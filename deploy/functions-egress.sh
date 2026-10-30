#!/bin/sh
# Generate host firewall rules that keep Edge Functions off the host and off private networks.
# A thin wrapper over lab/functions_egress.py: it prints rules or writes a file, and never
# changes a firewall. Read that module's docstring for the options and for what is not covered.
#
#   deploy/functions-egress.sh plan
#   deploy/functions-egress.sh verify-plan
#   deploy/functions-egress.sh apply --output /etc/sbarbase/functions-egress.nft
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/python3 "$here/../lab/functions_egress.py" "$@"
