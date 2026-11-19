#!/usr/bin/env bash
set -euo pipefail

if [[ "${DB_HOST:-}" != "" ]]; then
  echo "Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT:-5432}..."
  until nc -z "${DB_HOST}" "${DB_PORT:-5432}"; do
    sleep 1
  done
fi

if [[ "${DJANGO_RUN_MIGRATIONS:-0}" == "1" ]]; then
  python manage.py migrate --noinput
fi

if [[ "${DJANGO_WAIT_FOR_MIGRATIONS:-0}" == "1" ]]; then
  echo "Waiting for Django migrations to be applied..."
  until python manage.py migrate --check --noinput >/dev/null 2>&1; do
    sleep 2
  done
fi

if [[ "${DJANGO_CREATE_SUPERUSER:-1}" == "1" ]]; then
  python manage.py create_superuser
fi

# Loads base/demo/demo_products.json once, on a fresh database only.
if [[ "${DJANGO_SEED_DEMO:-1}" == "1" ]]; then
  python manage.py seed_demo --once
fi

if [[ "${DJANGO_COLLECTSTATIC:-0}" == "1" ]]; then
  python manage.py collectstatic --noinput
fi

exec "$@"
