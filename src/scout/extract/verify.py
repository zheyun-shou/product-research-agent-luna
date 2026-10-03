"""Check that a value's raw text really appears in the source."""
_INVISIBLE = dict.fromkeys(map(ord, "\u200e\u200f\u200b"), None)


def normalise(text: str) -> str:
    return " ".join(text.translate(_INVISIBLE).split()).casefold()


def verify_raw(raw: str, source: str) -> bool:
    needle = normalise(raw)
    return bool(needle) and needle in normalise(source)
