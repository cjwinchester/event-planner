# Production-ish image: GeoDjango (GDAL/GEOS/PROJ) + WeasyPrint (Pango) system libs.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        gdal-bin libgdal-dev libgeos-dev libproj-dev \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b fonts-dejavu-core curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .

ENV DJANGO_SETTINGS_MODULE=config.settings.prod
RUN bash bin/tailwindcss-build.sh
RUN SECRET_KEY=build FIELD_ENCRYPTION_KEYS=ZGV2LW9ubHktZmllbGQtZW5jcnlwdGlvbi1rZXktMDA= \
    python manage.py collectstatic --noinput

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
