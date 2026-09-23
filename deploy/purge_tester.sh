#!/usr/bin/env bash
# Explicit alias for the default, permanent tester removal.
set -euo pipefail
exec "$(dirname "$0")/remove_tester.sh" "$@"
