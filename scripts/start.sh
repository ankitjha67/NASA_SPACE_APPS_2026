#!/usr/bin/env sh
# One command setup and start for macOS and Linux.
# All the work is done by scripts/run.py; this file only finds a Python to run it with.
# Arguments are passed through, for example:  scripts/start.sh --check
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"

for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        exec "$candidate" "$root/scripts/run.py" "$@"
    fi
done

echo "Python was not found. Install Python 3.11 or newer from https://www.python.org/downloads/ and run this again." >&2
exit 1
