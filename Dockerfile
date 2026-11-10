# The web app. No Qt: the server runs the bridge without it.
#
#   docker build -t mtg-goldfisher .
#   docker run -p 8000:8000 mtg-goldfisher
#
# The card database and the stored parse are built into the image from the
# Scryfall dumps in the repository, so a host with no persistent disk (Render's
# free tier) starts with everything it needs, and a restart costs nothing.
# Accounts, decks and history live in Postgres (DATABASE_URL), never here.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MTGFISH_NO_QT=1 \
    MTGFISH_DATA_DIR=/app/data \
    MTGFISH_HOST=0.0.0.0 \
    PORT=8000 \
    # The container is meant to sit behind a TLS proxy, whose address is not
    # known in advance; trust its X-Forwarded-* headers. Never publish the
    # container's port directly to the internet with this set.
    FORWARDED_ALLOW_IPS=*

WORKDIR /app
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown app /app/data
COPY --chown=app mtgfish ./mtgfish
COPY --chown=app cache/scryfall ./data/scryfall
COPY --chown=app cache/pool.json cache/comprehensive_rules.txt ./data/

USER app
# The card database (from the dumps) and the stored parse of every card, so
# the first game does not spend a minute reading abilities on a small CPU.
RUN python -c "from mtgfish.bootstrap import seed_card_database; print(seed_card_database(report=print))" \
 && test -s /app/data/cards.sqlite \
 && python -m mtgfish.tools.parse_store build

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=4)" || exit 1

CMD ["python", "-m", "mtgfish.web"]
