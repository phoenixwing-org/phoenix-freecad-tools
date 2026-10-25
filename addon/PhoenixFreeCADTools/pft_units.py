# SPDX-License-Identifier: Apache-2.0
"""Describe FreeCAD's internal unit basis from an explicit dimension signature.

This module does not read native objects, convert numeric values or infer a
user's display schema. Unknown signatures have no inferred unit description.
"""

_PFT_INTERNAL_UNIT_SYMBOLS = ("mm", "kg", "s", "A", "K", "mol", "cd", "deg")


def pft_internal_unit(signature):
    """Return independent internal-unit metadata, or None for an invalid signature."""
    if type(signature) not in (list, tuple):
        return None
    try:
        powers = list(signature)
    except Exception:
        return None
    if len(powers) != 8 or any(type(power) is not int or not -128 <= power <= 127 for power in powers):
        return None
    unit = "*".join(symbol if power == 1 else "%s^%s" % (symbol, power)
                    for symbol, power in zip(_PFT_INTERNAL_UNIT_SYMBOLS, powers) if power) or "1"
    return {"unit": unit, "unit_signature": powers, "unit_system": "freecad_internal"}
