import threading

import pytest

from scout.db.conn import ThreadConnections
from scout.db.store import log_fetch

pytestmark = pytest.mark.db


def test_each_thread_with_own_gets_its_own_connection_and_the_main_thread_sees_what_it_stored(db):
    conns = ThreadConnections(db, size=2)
    log_fetch(conns, "main", "u0", "ok", 200, 1)  # through the main connection
    conns.execute("INSERT INTO fetch_log (source, url, status) VALUES ('main', 'open', 'ok')")  # not committed
    conns.share()  # before the threads: the open transaction closes
    seen, both = [], threading.Barrier(2)

    def researcher(n):
        with conns.own():
            seen.append(id(conns.current()))
            both.wait(5)  # both hold a connection at the same time
            conns.execute("INSERT INTO fetch_log (source, url, status) VALUES (%s, %s, 'ok')", (f"r{n}", f"u{n}"))
    threads = [threading.Thread(target=researcher, args=(n,)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert len(set(seen)) == 2 and id(db) not in seen
        assert conns.current() is db
        rows = conns.execute("SELECT source FROM fetch_log ORDER BY source").fetchall()
        assert [r["source"] for r in rows] == ["main", "main", "r1", "r2"]
    finally:
        conns.close()
