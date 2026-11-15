"""Bounded private vocabulary data; never log or hard-code personal entries."""
import json
import os
from pathlib import Path

MAX_PROMPT_BYTES = 256


def validate(names):
    if not isinstance(names, list) or len(names) > 32:
        raise ValueError('Invalid private name hints')
    seen = set()
    for name in names:
        if (not isinstance(name, str) or not 1 <= len(name) <= 48 or name.strip() != name
                or not all(c.isalpha() or c in " '-" for c in name) or not any(c.isalpha() for c in name)
                or name.casefold() in seen):
            raise ValueError('Invalid private name hints')
        seen.add(name.casefold())
    if len(prompt(names).encode()) > MAX_PROMPT_BYTES:
        raise ValueError('Private name hints exceed prompt budget')
    return names


def prompt(names):
    return 'Names: ' + ', '.join(names) + '.' if names else ''


def read(path, missing_ok=False):
    path = Path(path).absolute()
    if missing_ok and not os.path.lexists(path):
        return {'schema': 1, 'names': []}
    if (path.resolve() != path or not path.is_file() or path.stat().st_uid != os.geteuid()
            or path.stat().st_mode & 0o077 or path.stat().st_size > 4096
            or any((parent/'.git').exists() for parent in [path, *path.parents])):
        raise ValueError('Unsafe private vocabulary file')
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict) or set(data) != {'schema', 'names'} or type(data['schema']) is not int or data['schema'] != 1:
        raise ValueError('Invalid private vocabulary schema')
    validate(data['names'])
    return data
