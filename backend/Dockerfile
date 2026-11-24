# Phase 13a : image de base figée (version et variante Debian), dépendances figées avec empreintes.
FROM python:3.12.16-slim-trixie

ARG GIT_COMMIT=local
LABEL org.opencontainers.image.title="akwaba-backend" \
      org.opencontainers.image.revision="${GIT_COMMIT}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# requirements.lock : versions exactes + empreintes sha256 (généré depuis requirements.txt).
COPY requirements.lock .
RUN pip install --require-hashes -r requirements.lock

RUN useradd --system --uid 10001 --no-create-home akwaba

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

USER akwaba
EXPOSE 8000

# Un seul worker : le serveur dispose de moins de 1 Go de RAM.
# --no-proxy-headers (issue #8) : uvicorn ne réécrit jamais l'IP client depuis X-Forwarded-For ;
# seule l'application décide, via TRUSTED_PROXY_IPS (Nginx) et X-Real-IP.
# Racine en lecture seule à l'exécution (docker-compose.yml) : seul /tmp (tmpfs) est inscriptible.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
