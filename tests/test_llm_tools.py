import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from scout.llm.client import LLM, LLMOutputError, text_tool_calls, tool_name, tool_spec
from tests.test_llm import CONFIG, FakeClock


class RemovePicks(BaseModel):
    """Remove picks."""
    picks: list[int]


class Reply(BaseModel):
    """The message to the shopper."""
    text: str


def called(*calls):
    """A reply with real tool calls: (name, arguments) pairs."""
    tool_calls = [SimpleNamespace(function=SimpleNamespace(name=n, arguments=json.dumps(a))) for n, a in calls]
    return SimpleNamespace(content=None, tool_calls=tool_calls)


def written(text):
    """A reply with the tool calls as text."""
    return SimpleNamespace(content=text, tool_calls=None)


class FakeCompletions:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=self.replies.pop(0))],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


def make(replies):
    completions, clock = FakeCompletions(replies), FakeClock()
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return LLM(CONFIG, clients={"gemini": fake}, sleep=clock.sleep, clock=clock.clock), completions


def test_tool_calls_come_back_as_tool_objects_in_order():
    llm, completions = make([called(("remove_picks", {"picks": [2]}), ("reply", {"text": "Removed pick 2."}))])
    calls = llm.tools("chat", "sys", "user", [RemovePicks, Reply], name="Act")
    assert calls == [RemovePicks(picks=[2]), Reply(text="Removed pick 2.")]
    request = completions.requests[0]
    assert request["tool_choice"] == "required"
    assert [t["function"]["name"] for t in request["tools"]] == ["remove_picks", "reply"]
    assert [(c.schema, c.valid) for c in llm.calls] == [("Act", True)]


def test_tool_calls_written_as_text_are_accepted():
    text = '[[{"name": "remove_picks", "parameters": {"picks": [2]}}, {"name": "reply", "parameters": {"text": "ok"}}]]'
    llm, _ = make([written(text)])
    assert llm.tools("chat", "sys", "user", [RemovePicks, Reply]) == [RemovePicks(picks=[2]), Reply(text="ok")]
    # Nemotron, 2026-10-05: the outer brackets do not match.
    broken = '[[ {"name": "remove_picks", "parameters": {"picks": [5]}}, {"name": "reply", "parameters": {"text": "x"}} ]'
    assert text_tool_calls(broken) == [("remove_picks", '{"picks": [5]}'), ("reply", '{"text": "x"}')]


def test_a_wrong_call_is_sent_back_and_tried_again():
    llm, completions = make([called(("delete", {"picks": [2]})), written("I removed it."),
                             called(("remove_picks", {"picks": [2]}))])
    assert llm.tools("chat", "sys", "user", [RemovePicks, Reply]) == [RemovePicks(picks=[2])]
    assert "there is no tool delete" in completions.requests[1]["messages"][-1]["content"]
    assert "no tool call" in completions.requests[2]["messages"][-1]["content"]
    assert completions.requests[2]["messages"][-2] == {"role": "assistant", "content": "I removed it."}
    assert [c.valid for c in llm.calls] == [False, False, True]
    assert json.loads(llm.calls[0].output) == [["delete", '{"picks": [2]}']]
    assert json.loads(llm.calls[1].output) == "I removed it."
    llm.mark_checked(False, ["there is no pick 2"])
    assert (llm.calls[-1].checked, llm.calls[-1].problems) == (False, ["there is no pick 2"])


def test_three_wrong_replies_raise():
    llm, _ = make([written("no")] * 3)
    with pytest.raises(LLMOutputError, match="no valid tool calls"):
        llm.tools("chat", "sys", "user", [Reply])


def test_tool_names_and_specs():
    assert tool_name(RemovePicks) == "remove_picks"
    spec = tool_spec(RemovePicks)["function"]
    assert spec["description"] == "Remove picks." and spec["parameters"]["required"] == ["picks"]
    assert "title" not in spec["parameters"]
    assert text_tool_calls("not json") == [] and text_tool_calls('{"text": "no name"}') == []


def test_with_text_reply_the_model_can_answer_without_a_tool():
    llm, completions = make([written("Pick 2 is 177 cm high.")])
    assert llm.tools("chat", "sys", "user", [RemovePicks, Reply], text_reply=Reply) == [
        Reply(text="Pick 2 is 177 cm high.")]
    assert completions.requests[0]["tool_choice"] == "auto"
    assert json.loads(llm.calls[0].output) == [["reply", json.dumps({"text": "Pick 2 is 177 cm high."})]]
    both = SimpleNamespace(content="Done.", tool_calls=called(("remove_picks", {"picks": [2]})).tool_calls)
    llm, _ = make([both])
    assert llm.tools("chat", "sys", "user", [RemovePicks, Reply], text_reply=Reply) == [
        RemovePicks(picks=[2]), Reply(text="Done.")]
    assert json.loads(llm.calls[0].output)[-1] == ["reply", json.dumps({"text": "Done."})]  # the grader sees it
    llm, completions = make([written("no tool")] * 3)
    with pytest.raises(LLMOutputError):
        llm.tools("chat", "sys", "user", [Reply])  # without text_reply a tool call is still required
    assert completions.requests[0]["tool_choice"] == "required"
