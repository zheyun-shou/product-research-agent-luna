from scout.agent.followup import Reply, act
from scout.agent.lookup import ImageAnswer, Lookup, QueryProducts, ReadImages, ReadProduct, image_reader
from scout.needs import ConditionSpec, Needs
from scout.profiles import load_profile
from tests.fakes import FakeLLM
from tests.test_followup import CARDS, PICKS

TEXTS = {1: {"specs": {"Timer": "Ja"}, "bullets": ["Tyst, 39 dB"], "description": "Kyl med 24 h timer.",
             "reviews": [{"title": "Tyst", "rating": "5", "date": "2 maj 2026", "verified": True,
                          "text": "Hörs inte alls."}],
             "images": ["https://m.media-amazon.com/images/I/a.jpg", "https://evil.example/b.jpg"]}}


def make_lookup(query=lambda profile, needs: CARDS[1:], images=None):
    return Lookup(load_profile("fridge"), Needs(category="fridge", kind="fridge_freezer", budget=9000), CARDS,
                  lambda ids: {i: TEXTS[i] for i in ids if i in TEXTS}, query,
                  images or (lambda urls, question: ImageAnswer(found=True, answer=f"{len(urls)} images: 3 shelves")))


def test_read_product_gives_the_page_text_and_the_reviews():
    text = make_lookup().run(ReadProduct(picks=[1, 2, 7], question="timer"))
    assert "Pick 1: Fridge 1" in text and "Timer: Ja" in text and "- Tyst, 39 dB" in text
    assert "Description: Kyl med 24 h timer." in text
    assert "Reviews on the product page (1, a sample, not all reviews):" in text
    assert "- 5 of 5, verified purchase, 2 maj 2026: Tyst. Hörs inte alls." in text
    assert "Pick 2: no page text is stored" in text and "There is no pick 7." in text


def test_query_products_adds_conditions_and_marks_the_picks():
    seen = []
    lookup = make_lookup(query=lambda profile, needs: seen.append(needs) or CARDS[1:])
    text = lookup.run(QueryProducts(conditions=[ConditionSpec(attribute="height", op="<=", value=180)], budget=4000))
    assert seen[0].budget == 4000 and seen[0].conditions[0].attribute == "height" and seen[0].kind == "fridge_freezer"
    assert "Result 1 (pick 2): Fridge 2" in text and "They are not picks." in text
    assert "not number attributes" in lookup.run(QueryProducts(conditions=[
        ConditionSpec(attribute="colour", op="<=", value=1)]))


def test_read_images_uses_only_the_image_server():
    asked = []
    lookup = make_lookup(images=lambda urls, q: asked.append(urls) or ImageAnswer(found=True, answer="3 shelves"))
    assert lookup.run(ReadImages(pick=1, question="shelves")) == "read_images (pick 1, shelves): 3 shelves"
    assert asked == [["https://m.media-amazon.com/images/I/a.jpg"]]
    assert "no images are stored" in lookup.run(ReadImages(pick=2, question="x"))


def test_image_reader_sends_the_images_to_the_worker():
    llm = FakeLLM([ImageAnswer(found=True, answer="A drawing gives 186 cm.")])
    read = image_reader(llm, fetch=lambda url: "data:image/jpeg;base64,AAAA")
    assert read(["https://m.media-amazon.com/x.jpg"], "height").answer == "A drawing gives 186 cm."
    assert llm.requests[0][0] == "worker" and llm.images == ["data:image/jpeg;base64,AAAA"]


def test_act_looks_up_then_answers_with_the_numbers_it_found():
    llm = FakeLLM([[ReadProduct(picks=[1], question="timer")], "Yes, pick 1 has a 24 h timer."])
    result = act(llm, PICKS, CARDS, [], "Shopper: has 1 a timer?", set(), "has 1 a timer?", lookup=make_lookup())
    assert result.reply == Reply(text="Yes, pick 1 has a 24 h timer.") and result.lookups == ["read_product"]
    assert "What you looked up in this turn:\nread_product (timer):" in llm.requests[1][1]
    assert [c.schema for c in llm.calls] == ["Lookup", "Act"] and llm.calls[1].checked is True


def test_after_three_lookups_the_model_must_answer():
    look = [ReadProduct(picks=[1], question="x")]
    llm = FakeLLM([look, look, look, look, "Pick 1 is quiet."])
    result = act(llm, PICKS, CARDS, [], "Shopper: quiet?", set(), "quiet?", lookup=make_lookup())
    assert result.reply.text == "Pick 1 is quiet." and len(result.lookups) == 3
    assert "you looked up enough" in llm.requests[4][1]


def test_search_text_searches_the_category_and_marks_the_picks():
    from scout.agent.lookup import SearchText
    from scout.text.retrieve import Hit
    asked = []

    def search(question, scope, k):
        asked.append((question, scope, k))
        return [Hit(9, CARDS[1].listing_id, "review", "review 2", "Product: Fridge 2\nReview: loud at night"),
                Hit(8, 999, "review", "review 1", "Product: Other\nReview: quiet")]
    lookup = make_lookup()
    lookup.search = search
    text = lookup.run(SearchText(question="noise", kinds=["review"]))
    assert asked[0][1].category == "fridge" and asked[0][1].listing_ids is None and asked[0][1].kinds == ["review"]
    assert "[1] pick 2, review 2:\nProduct: Fridge 2\nReview: loud at night" in text
    assert "[2] not a pick, review 1:" in text
    lookup.run(SearchText(question="noise", picks=[1]))
    assert asked[1][1].listing_ids == [CARDS[0].listing_id]
    assert f"there are only {len(CARDS)} picks" in lookup.run(SearchText(question="x", picks=[7]))


def test_search_text_without_a_search_says_so():
    from scout.agent.lookup import SearchText
    assert "not available" in make_lookup().run(SearchText(question="noise"))
