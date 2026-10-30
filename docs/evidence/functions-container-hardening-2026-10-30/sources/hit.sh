#!/bin/bash
# usage: hit.sh HOSTPORT   (each function once, with the status line)
for f in hello sbjs sbnpm npmfn probe; do echo "== $f"; curl -s -m 90 -w ' [%{http_code}]\n' 127.0.0.1:$1/$f; done
