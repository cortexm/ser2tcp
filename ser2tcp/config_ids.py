"""Stable identifiers for the entries in a configuration.

Ports and HTTP servers used to be addressed by their position in the
config. A position renumbers: an entry that fails to start, or one
somebody else removes, shifts every entry after it, so a request aimed
at one lands on another. An id is handed out once, written back to the
file, and never changes after that.
"""

import hashlib as _hashlib
import json as _json
import re as _re
import secrets as _secrets

# Long enough that a collision is not a practical concern, short enough
# to read in a URL.
ID_BYTES = 4

# Enough of the digest to make a collision between two edits of the
# same entry unthinkable, short enough to travel in a form field.
REV_CHARS = 16

# An id travels in a URL and names an entry in a config file, so it is
# kept to what survives both untouched: no spaces, no slashes, nothing
# outside ASCII. Choosing your own readable one is the point of the
# rule being this permissive.
_ID_RE = _re.compile(r'^[A-Za-z0-9._-]{1,64}$')


def is_valid_id(value):
    """True if `value` may be used as an id"""
    return isinstance(value, str) and bool(_ID_RE.match(value))


def _entries(configuration):
    """Every entry that carries an id, ports first then HTTP servers"""
    ports = configuration.get('ports', [])
    if isinstance(configuration, list):
        ports = configuration
    http = configuration.get('http', []) \
        if isinstance(configuration, dict) else []
    if isinstance(http, dict):
        http = [http]
    for entry in list(ports) + list(http):
        if isinstance(entry, dict):
            yield entry


def taken_ids(configuration):
    """Every id already in use in this configuration"""
    return {entry['id'] for entry in _entries(configuration)
            if entry.get('id')}


def fresh_id(configuration):
    """An id no entry in this configuration is using"""
    taken = taken_ids(configuration)
    while True:
        candidate = _secrets.token_hex(ID_BYTES)
        if candidate not in taken:
            return candidate


def assign_ids(configuration):
    """Give every port and HTTP server an id; True if any were added.

    A duplicate is replaced rather than kept: two entries answering to
    the same id is worse than the renumbering this replaces.
    """
    changed = False
    seen = set()
    for entry in _entries(configuration):
        entry_id = entry.get('id')
        if isinstance(entry_id, str) and entry_id and entry_id not in seen:
            seen.add(entry_id)
            continue
        while True:
            new_id = _secrets.token_hex(ID_BYTES)
            if new_id not in seen:
                break
        entry['id'] = new_id
        seen.add(new_id)
        changed = True
    return changed


def entry_rev(entry):
    """A short fingerprint of what an entry currently says.

    Two admins with the same port open used to overwrite each other in
    silence: the second save wrote back a copy read before the first one
    landed, and neither was told. A save can carry the revision it read
    and be refused when the entry has moved on since.

    It is derived from the content rather than stored, so nothing extra
    lands in config.json, an entry edited by hand is covered too, and
    the revision cannot go stale against the thing it describes.
    """
    payload = {key: value for key, value in entry.items() if key != 'rev'}
    canonical = _json.dumps(
        payload, sort_keys=True, separators=(',', ':'), default=str)
    digest = _hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    return digest[:REV_CHARS]
