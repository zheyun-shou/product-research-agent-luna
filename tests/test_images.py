import httpx
import pytest

from scout.extract.images import fetch_image_bytes, read_image_texts, segments, usable


def test_usable_text():
    assert usable("Energy class E, 203 kWh/annum, 41 dB")
    assert not usable("0") and not usable("| |")
    assert not usable("\n".join(["A | E"] * 20))  # a model that loops


class Picture:
    def __init__(self, width, height, box=None):
        self.size, self.box = (width, height), box

    def crop(self, box):
        return Picture(box[2] - box[0], box[3] - box[1], box)


def test_a_long_image_is_cut_with_an_overlap():
    assert len(segments(Picture(800, 1500))) == 1
    parts = segments(Picture(1000, 4000))
    assert [p.box for p in parts] == [(0, 0, 1000, 1600), (0, 1500, 1000, 3100), (0, 3000, 1000, 4000)]


def test_only_the_image_server_and_text_for_each_loaded_image():
    with pytest.raises(ValueError):
        fetch_image_bytes("https://www.amazon.se/dp/B0AAAAAAA1")

    class Reader:
        def read(self, data):
            return {b"label": "Energy class E, 41 dB", b"photo": "0"}[data]

    def fetch(url):
        if url.endswith("gone.jpg"):
            raise httpx.HTTPError("404")
        return b"label" if "label" in url else b"photo"
    found = read_image_texts(["https://x/label.jpg", "https://x/photo.jpg", "https://x/gone.jpg"], Reader(), fetch)
    assert [(f.url[-9:], f.text) for f in found] == [("label.jpg", "Energy class E, 41 dB"), ("photo.jpg", "")]
