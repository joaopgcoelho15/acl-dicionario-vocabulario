FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY public_app ./public_app
COPY editorial_app ./editorial_app
COPY contracts ./contracts
COPY acceptance ./acceptance

RUN pip install --no-cache-dir .

EXPOSE 8000 8001

CMD ["acl-reference", "--help"]
