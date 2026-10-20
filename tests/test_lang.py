from scout.lang import TEXTS, detect, t, turn_lang


def test_detect_gives_the_language_of_a_message():
    assert detect("我想买一个冰箱") == "zh"
    assert detect("Jag vill ha ett tyst kylskåp") == "sv"
    assert detect("kylskåp under 6000 kr") == "sv"
    assert detect("I want a quiet fridge for my kitchen") == "en"
    assert detect("ok") is None
    assert detect("2000") is None


def test_turn_lang_keeps_the_language_before_when_the_message_shows_no_sign():
    messages = [{"role": "user", "content": "我想买冰箱"}, {"role": "assistant", "content": "Which size?"},
                {"role": "user", "content": "180 cm"}]
    assert turn_lang(messages, "zh") == "zh"
    assert turn_lang(messages) == "en"
    assert turn_lang(messages[:1], "en") == "zh"


def test_t_fills_in_values_and_plurals():
    assert t("step_read_product", "en", n=1) == "Read the stored pages of 1 product"
    assert t("step_read_product", "en", n=3) == "Read the stored pages of 3 products"
    assert t("step_read_product", "sv", n=3) == "Läs sparade sidor för 3 produkter"
    assert t("step_read_product", "zh", n=3) == "读取 3 个商品的已存页面"
    assert t("no_ratings", "de") == "no ratings"


def test_each_text_has_all_languages_and_the_same_values():
    import string
    for key, texts in TEXTS.items():
        assert set(texts) == {"en", "sv", "zh"}, key
        names = [{f for _, f, _, _ in string.Formatter().parse(text) if f} for text in texts.values()]
        assert all(n == names[0] for n in names), key
