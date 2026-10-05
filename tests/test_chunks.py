from scout.text.chunks import page_chunks, split_text


def words(text):
    return len(text.split())


def test_short_text_is_one_part():
    assert split_text("One line.\nTwo lines.", words) == ["One line.\nTwo lines."]


def test_long_text_is_divided_at_paragraphs_with_an_overlap():
    paras = [f"p{i} x x x" for i in range(6)]  # 4 words each
    parts = split_text("\n\n".join(paras), words, max_tokens=10, overlap=4)
    assert parts[0] == "\n".join(paras[:2])
    assert parts[1].startswith(paras[1].strip())  # the last paragraph of the part before
    assert all(words(p) <= 10 for p in parts)
    assert paras[5].strip() in parts[-1]


def test_a_long_paragraph_is_divided_at_sentences():
    para = " ".join(f"Sentence {i} is here." for i in range(10))  # 4 words each
    parts = split_text(para, words, max_tokens=9, overlap=0)
    assert parts[0] == "Sentence 0 is here.\nSentence 1 is here."
    assert len(parts) == 5


def test_page_chunks_start_with_the_title_and_never_keep_the_author():
    page = {"specs": {"Höjd": "186 cm"}, "bullets": ["Tyst"], "description": "Kyl med timer.",
            "reviews": [{"title": "Bra", "rating": "5", "date": "2 maj 2026", "verified": True, "text": "Tyst.",
                         "author": "Anna"}]}
    chunks = page_chunks("Fridge X", page)
    assert [c.kind for c in chunks] == ["specs", "description", "review"]
    assert all(c.text.startswith("Product: Fridge X\n") for c in chunks)
    assert chunks[0].text.endswith("Höjd: 186 cm\n- Tyst")
    assert chunks[2].text.endswith("Review (5 of 5, verified purchase, 2 maj 2026): Bra. Tyst.")
    assert chunks[2].ref == "review 1" and "author" not in chunks[2].meta
    assert "Anna" not in " ".join(c.text for c in chunks)


def test_an_empty_page_has_no_chunks():
    assert page_chunks("X", {"specs": {}, "bullets": [], "description": "", "reviews": []}) == []
