"""
Smart import helpers: derive part metadata from file names.

Used by the project ZIP import (fallback when the manifest lacks data) and
mirrored in the Add Parts frontend (add_parts.html) for pre-fill. Keep both
in sync.
"""
import os
import re

# Quantity tokens at the end of a name, separated from the rest by space/_/-.
#   bracket_x3 / bracket x3 / bracket-x3        -> 3
#   bracket_3x                                  -> 3
#   bracket_3pcs / bracket_3pc / bracket_3 pieces -> 3
_QUANTITY_PATTERNS = [
    re.compile(r'^(?P<name>.*?)[ _\-]x(?P<qty>\d+)$', re.IGNORECASE),
    re.compile(r'^(?P<name>.*?)[ _\-](?P<qty>\d+)x$', re.IGNORECASE),
    re.compile(r'^(?P<name>.*?)[ _\-](?P<qty>\d+)\s*(?:pcs?|pieces?)$', re.IGNORECASE),
]

_SIDE_RE = re.compile(r'^(?P<base>.*?)\s*\((?P<side>left|right)\)\s*$', re.IGNORECASE)
_MIRROR_RE = re.compile(r'(?i)(?:^|[\s_-])mirror(?:$|[\s_-])')


def parse_quantity_from_name(name):
    """
    Parse a quantity token out of a part/file name (without extension).

    Returns (cleaned_name, quantity). Conservative: only trailing tokens
    separated by space/underscore/hyphen are recognized. A trailing
    (Left)/(Right) side marker and any 'mirror' token are ignored while
    matching, so e.g. 'earcup mirror x2 (Left)' yields ('earcup (Left)', 2);
    the side marker is reattached to the cleaned name. No match returns
    (name, 1). If stripping the token would empty the name (or yield an
    invalid quantity), the original name is kept with quantity 1.
    """
    stripped = (name or '').strip()
    side_match = _SIDE_RE.match(stripped)
    core = _strip_mirror_token(side_match.group('base') if side_match else stripped)
    for pattern in _QUANTITY_PATTERNS:
        match = pattern.match(core)
        if match:
            cleaned = match.group('name').strip()
            quantity = int(match.group('qty'))
            if not cleaned or quantity < 1:
                return stripped, 1
            suffix = f" ({side_match.group('side')})" if side_match else ''
            return cleaned + suffix, quantity
    return stripped, 1


def resolve_manifest_quantity(part_data):
    """
    Resolve a part's quantity from a manifest entry.

    Prefers an explicit valid integer quantity, falls back to parsing the
    STL file name (then the part name), and defaults to 1. This keeps ZIP
    imports working for manifests that omit the quantity key.
    """
    quantity = part_data.get('quantity')
    if isinstance(quantity, int) and not isinstance(quantity, bool) and quantity >= 1:
        return quantity

    stl_file = part_data.get('stl_file')
    if stl_file:
        base_name = os.path.splitext(os.path.basename(stl_file))[0]
        _, parsed = parse_quantity_from_name(base_name)
        if parsed > 1:
            return parsed

    name = part_data.get('name')
    if name:
        _, parsed = parse_quantity_from_name(name)
        if parsed > 1:
            return parsed

    return 1


def mirror_pair_names(name):
    """
    Return (main_name, counterpart_name) for a mirror pair.

    Follows the copy_mirror_part convention: an unmarked part becomes
    '(Right)' with an '(Left)' counterpart; a marked part keeps its name.

    'earcup'         -> ('earcup (Right)', 'earcup (Left)')
    'earcup (Left)'  -> ('earcup (Left)', 'earcup (Right)')
    'earcup (Right)' -> ('earcup (Right)', 'earcup (Left)')
    """
    stripped = (name or '').strip()
    match = _SIDE_RE.match(stripped)
    if match:
        base = match.group('base').strip()
        opposite = 'Right' if match.group('side').lower() == 'left' else 'Left'
        return stripped, f"{base} ({opposite})"
    return f"{stripped} (Right)", f"{stripped} (Left)"


def mirror_counterpart_name(name):
    """Return the Left/Right counterpart name for a part."""
    return mirror_pair_names(name)[1]


def is_mirror_marked(name):
    """Return True if the name ends with a (Left)/(Right) marker."""
    return bool(_SIDE_RE.match((name or '').strip()))


def _strip_mirror_token(name):
    """Remove isolated 'mirror' word from name, case-insensitive."""
    if not name:
        return name
    new = _MIRROR_RE.sub(' ', name)
    new = re.sub(r'\s+', ' ', new).strip()
    new = new.strip(' _-')
    return new


def is_mirror_indicator(name):
    """Return True if name contains a side marker or a mirror token."""
    if not name:
        return False
    stripped = (name or '').strip()
    if is_mirror_marked(stripped):
        return True
    return _strip_mirror_token(stripped) != stripped


def clean_import_name(name):
    """
    Clean a name for import: parse quantity, strip mirror token.
    Returns (cleaned_name, quantity, mirror_indicator).
    """
    mirror_indicator = is_mirror_indicator(name)
    cleaned, qty = parse_quantity_from_name(name)
    return _strip_mirror_token(cleaned), qty, mirror_indicator
