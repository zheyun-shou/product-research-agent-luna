"""Vectors and tokens for the chunks (spec 4.6). bge-m3 runs on the local computer."""
import re
import threading
from dataclasses import dataclass
from typing import Protocol

BGE_M3 = "BAAI/bge-m3"

# bge-m3 and the reranker load and run one at a time. On 2026-10-26 the app stopped with "double free or
# corruption" while search_text and search_web started the two models at the same time
MODELS = threading.RLock()

# A model number has letters and digits, for example RB38C7B5D22/EF. Subword tokens break it into parts, so BM25
# also gets it as one token.
MODEL_NUMBER = re.compile(r"(?<![\w-])(?=[\w/-]*\d)(?=[\w/-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9/-]{4,}(?![\w-])")


def model_numbers(text: str) -> list[str]:
    out = []
    for match in MODEL_NUMBER.findall(text):
        whole = match.upper().strip("/-")
        out.append(whole)
        parts = [p for p in re.split(r"[/-]", whole) if len(p) >= 5 and re.search(r"\d", p) and re.search(r"[A-Z]", p)]
        out += [p for p in parts if p != whole]
    return out


@dataclass
class Embedded:
    dense: list[float]
    sparse: dict[str, float]  # token -> weight, from the sparse output of bge-m3


class Embedder(Protocol):
    name: str  # the model and its version, stored with each chunk

    def embed(self, texts: list[str]) -> list[Embedded]: ...

    def subwords(self, text: str) -> list[str]: ...

    def count(self, text: str) -> int: ...


def bm25_tokens(embedder: Embedder, text: str) -> list[str]:
    """The tokens of the BM25 index: lowercase subwords of the bge-m3 tokenizer, and the model numbers."""
    return [t for t in embedder.subwords(text.lower()) if t.strip("▁")] + model_numbers(text)


class BgeM3Embedder:
    """bge-m3 through FlagEmbedding, on the GPU in fp16 when there is one. The first use downloads the model
    into the Hugging Face cache."""

    def __init__(self, model: str = BGE_M3, batch_size: int = 16, max_length: int = 1024):
        import torch
        from FlagEmbedding import BGEM3FlagModel

        self.model = BGEM3FlagModel(model, use_fp16=torch.cuda.is_available())
        self.tokenizer = self.model.tokenizer
        self.batch_size, self.max_length = batch_size, max_length
        self.name = model

    def embed(self, texts: list[str]) -> list[Embedded]:
        if not texts:
            return []
        out = self.model.encode(texts, batch_size=self.batch_size, max_length=self.max_length,
                                return_dense=True, return_sparse=True)
        result = []
        for dense, weights in zip(out["dense_vecs"], out["lexical_weights"]):
            ids = [int(i) for i in weights]
            tokens = self.tokenizer.convert_ids_to_tokens(ids)
            result.append(Embedded([float(x) for x in dense],
                                   {tok: float(weights[str(i)]) for tok, i in zip(tokens, ids)}))
        return result

    def subwords(self, text: str) -> list[str]:
        return self.tokenizer.tokenize(text)

    def count(self, text: str) -> int:
        return len(self.tokenizer.tokenize(text))
