"""Attributes that are variants of a listing, for example the shoe size. Each size has its own page, price and stock.

The value of such an attribute is the list of sizes that the shop has, in the colour of the listing or in another
colour. A condition passes when one size is inside it. Before the picks, Python opens the page of the size of the
shopper, in the colour of the listing when it has that size."""
import re

from scout.needs import Needs
from scout.picks.rank import inside
from scout.profiles import CategoryProfile
from scout.sources.base import ProductRecord

# "38 EU", "38 2/3 EU", "43,5", "40.5 EU". Sizes in another system ("UK 5", "US 7") are not read.
_SIZE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)(?:\s+(\d)/(\d))?\s*(\w+)?\s*$")


def size_number(label: str, unit: str | None) -> float | None:
    m = _SIZE.match(label)
    if not m or (m[4] and unit and m[4].lower() != unit.lower()):
        return None
    number = float(m[1].replace(",", "."))
    return round(number + int(m[2]) / int(m[3]), 2) if m[2] else number


def variant_attributes(rec: ProductRecord, profile: CategoryProfile) -> dict[str, dict]:
    """For each variant attribute of the profile: the values of the variants, and the product id of the page of
    each value. A value in the other dimensions of this page (the same colour) comes first; a value that only
    another colour has gets that colour in its name."""
    current = rec.options.get(rec.source_id)
    out = {}
    for attr in profile.attributes:
        if not attr.variant or not current or attr.variant not in current:
            continue
        others = {k: v for k, v in current.items() if k != attr.variant}
        pages = {}
        # The same colour first, then the others in the order of the page
        for asin, dims in sorted(rec.options.items(), key=lambda item: any(item[1].get(k) != v
                                                                          for k, v in others.items())):
            number = size_number(dims.get(attr.variant, ""), attr.unit)
            if number is None or number in pages:
                continue
            other = [v for k, v in dims.items() if k != attr.variant and others.get(k) != v]
            pages[number] = (asin, dims[attr.variant] + (f" (only in {' / '.join(other)})" if other else ""))
        if pages:
            ordered = sorted(pages)
            names = ", ".join(pages[n][1] for n in ordered)
            # No "unit": the names have it ("38 EU")
            out[attr.name] = {"value": names, "values": ordered, "raw": names,
                              "source_url": rec.url, "pages": {f"{n:g}": pages[n][0] for n in ordered},
                              "names": {f"{n:g}": pages[n][1] for n in ordered}}
    return out


def size_page(attributes: dict, needs: Needs, profile: CategoryProfile) -> tuple[str, str] | None:
    """(the name of the value, its product id) for the first variant value inside all conditions of the needs on a
    variant attribute. None when the needs have no such condition. ("", "") when no value fits, or when the page
    gave no list of variants."""
    for attr in profile.attributes:
        conds = [c for c in needs.conditions if c.attribute == attr.name]
        entry = attributes.get(attr.name) or {}
        if not attr.variant or not conds:
            continue
        if "pages" not in entry:
            return "", ""
        for number in entry["values"]:
            if all(inside(number, c.op, c.value) for c in conds):
                key = f"{number:g}"
                return (entry.get("names") or {}).get(key, key), entry["pages"][key]
        return "", ""
    return None


