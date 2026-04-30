FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Europe/Bratislava

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scraper.py storage.py menus.py bot.py ./

VOLUME ["/data"]
ENV DB_PATH=/data/bazos.sqlite3

CMD ["python", "-u", "bot.py"]
