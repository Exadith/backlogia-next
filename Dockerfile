FROM ghcr.io/sam1am/backlogia:latest

WORKDIR /app

COPY web ./web
COPY requirements.txt ./requirements.txt

RUN pip install --no-cache-dir -r requirements.txt
