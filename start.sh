#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "$(uname -r)" in
    *[Mm]icrosoft*) ;;
    *) printf '%s\n' 'Huella requires WSL2 with Chrome installed in Windows.' >&2; exit 1 ;;
esac
cd "$ROOT"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
    printf '%s\n' 'Prepare the environment first: python3 -m venv .venv' 'Then: .venv/bin/python -m pip install -r requirements.txt' >&2
    exit 1
fi
exec "$ROOT/.venv/bin/python" -m app
