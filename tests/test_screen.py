from scout.extract.screen import Keep, screen_titles
from scout.profiles import load_profile
from scout.sources.base import Candidate
from tests.fakes import FakeLLM


def cand(n, title):
    return Candidate(source="amazon_se", source_id=f"B0AAAAAAA{n}", url=f"https://www.amazon.se/dp/B0AAAAAAA{n}",
                     title=title)


def test_keeps_only_the_numbers_the_worker_returns():
    cands = [cand(1, "Samsung kyl/frys"), cand(2, "Kylskåpstermometer"), cand(3, "Bosch kylskåp")]
    llm = FakeLLM([Keep(keep=[0, 2, 9])])
    kept = screen_titles(cands, load_profile("fridge"), llm)
    assert [c.source_id for c in kept] == ["B0AAAAAAA1", "B0AAAAAAA3"]
    role, prompt = llm.requests[0]
    assert role == "worker"
    assert "1: Kylskåpstermometer" in prompt and "Fridge" in prompt
