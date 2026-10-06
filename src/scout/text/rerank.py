"""A cross-encoder that scores how well a text answers a question (web search, phase 3 Task 9). bge-reranker-v2-m3
runs on the local computer and reads many languages. A test on 2026-10-07 showed why it is used for the web pages
and not the cosine of bge-m3: the cosine was high for menus that repeat the model name, and low for buyer reviews
about the noise that do not name the model."""

RERANKER = "BAAI/bge-reranker-v2-m3"


class BgeReranker:
    """The first use downloads the model into the Hugging Face cache. On the GPU in fp16 when there is one."""

    def __init__(self, model: str = RERANKER, batch_size: int = 16, max_length: int = 512):
        import torch
        from FlagEmbedding import FlagReranker

        self.model = FlagReranker(model, use_fp16=torch.cuda.is_available())
        self.batch_size, self.max_length = batch_size, max_length
        self.name = model

    def __call__(self, question: str, texts: list[str]) -> list[float]:
        """A score from 0 to 1 for each text."""
        if not texts:
            return []
        scores = self.model.compute_score([[question, t] for t in texts], batch_size=self.batch_size,
                                          max_length=self.max_length, normalize=True)
        return [float(s) for s in (scores if isinstance(scores, list) else [scores])]
