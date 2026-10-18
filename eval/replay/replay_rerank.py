"""Add a local reranker to the replay of the two newest runs: bge-m3 dense similarity of the card title to the
needs (the facts of the target without the budget line). Writes replay_rerank.json."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from replay_runs import main  # noqa: E402
from scout.text.embed import BgeM3Embedder  # noqa: E402

embedder = BgeM3Embedder(max_length=256)
cache: dict[str, np.ndarray] = {}


def vectors(texts):
    new = [t for t in dict.fromkeys(texts) if t not in cache]
    for t, e in zip(new, embedder.embed(new)):
        cache[t] = np.array(e.dense)
    return np.stack([cache[t] for t in texts])


def rerank(target, cards):
    need = " ".join(f for f in target.facts if "budget" not in f.lower())
    q = vectors([need])[0]
    m = vectors([c.title for c in cards])
    sims = m @ q / (np.linalg.norm(m, axis=1) * np.linalg.norm(q))
    return [cards[i] for i in np.argsort(-sims, kind="stable")]


if __name__ == "__main__":
    main(only={"1017-2037", "1018-1741"}, rerank=rerank, out_name="replay_rerank.json")
