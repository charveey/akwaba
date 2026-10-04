FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

RUN useradd --system --uid 10001 --no-create-home akwaba

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

USER akwaba
EXPOSE 8000

# Un seul worker : le serveur dispose de moins de 1 Go de RAM.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
