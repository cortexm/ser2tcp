"""Stable identifiers for the entries in a configuration.

Ports and HTTP servers used to be addressed by their position in the
config. A position renumbers: an entry that fails to start, or one
somebody else removes, shifts every entry after it, so a request aimed
at one lands on another. An id is handed out once, written back to the
file, and never changes after that.
"""

import hashlib as _hashlib
import json as _json
import os as _os
import re as _re

# An id is derived from the name, so it is as long as somebody cares to
# make it - cut short of the 64 the pattern allows, leaving room for the
# hyphen and number a collision adds.
SLUG_MAX = 48

# What a name is left with. Everything else becomes a hyphen, because an
# id travels in a URL and names an entry in a config file.
_SLUG_KEEP = _re.compile(r'[^a-z0-9]+')

# Nothing to derive one from: an entry with no name and no device. Its
# kind is the last thing left to say about it, and calling an HTTP
# server `port` would be worse than saying nothing.
FALLBACK_SLUG = 'port'
HTTP_FALLBACK_SLUG = 'http'

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


def slug(text):
    """A name as an id: lowercase, digits and hyphens, nothing else.

    Every other character becomes a hyphen, runs of them collapse to
    one, and neither end keeps one - so `My Port ##2` and `my/port--2`
    both come out `my-port-2`, which is the point: what a person types
    to mean the same thing should mean the same thing.

    Empty when there was nothing to keep, which the caller answers for.
    """
    if not isinstance(text, str):
        return ''
    return _SLUG_KEEP.sub('-', text.lower()).strip('-')[:SLUG_MAX].strip('-')


def entry_slug(entry, fallback=FALLBACK_SLUG):
    """What an entry's id should be derived from.

    Its name, or failing that the device it is configured for - so a
    port added over the API with no name still gets something readable
    rather than a number nobody chose. `fallback` is the last resort,
    and says which kind of entry this is.
    """
    base = slug(entry.get('name'))
    if base:
        return base
    serial = entry.get('serial')
    if isinstance(serial, dict):
        base = slug(_os.path.basename(str(serial.get('port') or '')))
    return base or fallback


def unique_id(base, taken):
    """`base`, or the first `base-N` nobody is answering to.

    Two names can land on one slug - `My Port` and `my.port` both give
    `my-port` - so the count is against the ids in use, not the names
    they came from.
    """
    base = base or FALLBACK_SLUG
    if base not in taken:
        return base
    number = 1
    while '%s-%d' % (base, number) in taken:
        number += 1
    return '%s-%d' % (base, number)


def _entries(configuration):
    """Every entry that carries an id, ports first then HTTP servers"""
    for entry, _ in _kinds(configuration):
        yield entry


def _kinds(configuration):
    """Every entry with the fallback its kind would use for an id"""
    ports = configuration.get('ports', [])
    if isinstance(configuration, list):
        ports = configuration
    http = configuration.get('http', []) \
        if isinstance(configuration, dict) else []
    if isinstance(http, dict):
        http = [http]
    for entry in ports:
        if isinstance(entry, dict):
            yield entry, FALLBACK_SLUG
    for entry in http:
        if isinstance(entry, dict):
            yield entry, HTTP_FALLBACK_SLUG


def taken_ids(configuration):
    """Every id already in use in this configuration"""
    return {entry['id'] for entry in _entries(configuration)
            if entry.get('id')}


def fresh_id(configuration, entry=None, fallback=FALLBACK_SLUG):
    """An id no entry in this configuration is using.

    Derived from the entry's name when there is one to derive it from,
    because an id is what the API and every link say - and `esp32` says
    it where `7f3a91c4` only points at it.
    """
    base = entry_slug(entry, fallback) if isinstance(entry, dict) \
        else fallback
    return unique_id(base, taken_ids(configuration))


def assign_ids(configuration):
    """Give every port and HTTP server an id; True if any were added.

    A duplicate is replaced rather than kept: two entries answering to
    the same id is worse than the renumbering this replaces.

    Only the missing ones: an id that is already written down is what
    links and scripts are using, whatever it looks like, so a config
    full of the old random ones keeps them.
    """
    changed = False
    seen = set()
    pending = []
    for entry, fallback in _kinds(configuration):
        entry_id = entry.get('id')
        if isinstance(entry_id, str) and entry_id and entry_id not in seen:
            seen.add(entry_id)
            continue
        pending.append((entry, fallback))
    for entry, fallback in pending:
        entry['id'] = unique_id(entry_slug(entry, fallback), seen)
        seen.add(entry['id'])
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
