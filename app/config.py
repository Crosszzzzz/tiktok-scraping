"""Explicit startup-only environment loading; importing this module reads no files."""

import os
from pathlib import Path
import re
import shlex

ROOT = Path(__file__).resolve().parent.parent


def load_environment(path=None, *, environ=None):
    """Load simple dotenv assignments without interpolation or process overrides.

    Tests supply an isolated file and mapping. Never call automatically on import.
    Malformed lines are ignored without logging their potentially secret contents.
    """
    target = os.environ if environ is None else environ
    try:
        contents = (ROOT / '.env' if path is None else Path(path)).read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return
    for line in contents.splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[7:].strip()
        name, separator, value = line.partition('=')
        name = name.strip()
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            continue
        try:
            lexer = shlex.shlex(value, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = '#'
            lexer.escape = ''  # Keep Windows path separators literal.
            tokens = list(lexer)
        except ValueError:
            continue
        if len(tokens) <= 1:
            target.setdefault(name, tokens[0] if tokens else '')
