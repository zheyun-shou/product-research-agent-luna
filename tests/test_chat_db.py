import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver

from scout.agent.clarify import CategoryChoice, ClarifyReply
from scout.agent.graph import build_graph
from scout.config import load_settings
from scout.db.conn import connect, connect_checkpoints, end_transaction
from scout.needs import Needs
from tests.fakes import FakeLLM
from tests.test_graph import World, run

pytestmark = pytest.mark.db
THREAD = "test-resume"


def test_a_thread_continues_after_a_restart():
    url = load_settings().test_database_url
    world = World()
    with connect_checkpoints(url) as conn:
        saver = PostgresSaver(conn)
        saver.setup()
        saver.delete_thread(THREAD)
        llm = FakeLLM([CategoryChoice(category="fridge"),
                       ClarifyReply(needs=Needs(), ready=False, question="Which kind?")])
        run(build_graph(world.deps(llm), saver), THREAD, "I want a fridge")
    with connect_checkpoints(url) as conn:
        saver = PostgresSaver(conn)
        graph = build_graph(world.deps(FakeLLM([])), saver)
        values = graph.get_state({"configurable": {"thread_id": THREAD}}).values
        assert values["messages"][-1]["content"] == "Which kind?"
        assert values["needs"]["category"] == "fridge"
        saver.delete_thread(THREAD)


def test_a_failed_statement_does_not_block_the_next_turn():
    with connect(load_settings().test_database_url) as conn:
        with pytest.raises(psycopg.errors.UndefinedTable):
            conn.execute("SELECT * FROM no_such_table")
        end_transaction(conn)
        assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1
