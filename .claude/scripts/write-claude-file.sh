#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 ]]; then echo "Usage: $0 <target>" >&2; exit 1; fi
target="$1"
if [[ "$target" != /* ]]; then target="$(pwd)/$target"; fi
mkdir -p "$(dirname "$target")"
python3 -c "
import sys, pathlib
p = pathlib.Path(sys.argv[1])
p.write_text(sys.stdin.read(), encoding='utf-8')
print(f'wrote to {p}', file=sys.stderr)
" "$target"
