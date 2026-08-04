FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir \
    pycryptodome \
    zstandard \
    requests \
    protobuf \
    json5

RUN git clone --depth 1 https://github.com/imLinguin/nile.git /opt/nile && \
    cd /opt/nile && \
    sed -i 's/dynamic = \["version"\]/version = "1.1.1"/' pyproject.toml && \
    printf '\n[tool.setuptools.packages.find]\ninclude = ["nile*"]\n' >> pyproject.toml && \
    pip install --no-cache-dir .

COPY web ./web

RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1
ENV DATABASE_PATH=/data/game_library.db

EXPOSE 5050

CMD ["uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "5050"]