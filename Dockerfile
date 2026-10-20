# The product-scout web app (phase 7). The database and SearXNG run in their own containers (docker-compose.yml).
# Chrome runs on the computer, not here: I log in and solve captchas in its window (scripts/start-browser.ps1).
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 UV_SYSTEM_PYTHON=1 HF_HOME=/root/.cache/huggingface
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.22

# torch for the CPU only: bge-m3 and the reranker run here without a GPU
RUN uv pip install --no-cache torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --no-cache -e ".[retrieval]"
COPY config ./config
COPY profiles ./profiles

EXPOSE 9000
CMD ["scout", "web", "--host", "0.0.0.0", "--port", "9000"]
