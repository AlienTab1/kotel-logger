#!/bin/sh
# Run from any directory; no downloads or remote code execution in this wrapper.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/python3 "$SCRIPT_DIR/install.py" "$@"
