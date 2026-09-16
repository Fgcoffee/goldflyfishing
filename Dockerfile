# The web app. No Qt: the server runs the bridge without it.
#
#   docker build -t mtg-goldfisher .
#   docker run -p 8000:8000 -v mtgfish-data:/data mtg-goldfisher
#
# The card database is not baked in (it is 99 MB and changes with Scryfall).
# Put cards.sqlite in the /data volume, or let the first start build it - see
# DEPLOY.md.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MTGFISH_NO_QT=1 \
    MTGFISH_DATA_DIR=/data \
    MTGFISH_HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app /data
COPY --chown=app mtgfish ./mtgfish

USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)" || exit 1

CMD ["python", "-m", "mtgfish.web"]
