# CubixGear Backend

Django REST API for the CubixGear workshop SaaS.

## Core stack

- Django 6
- Django REST Framework
- JWT access/refresh tokens
- CORS
- SQLite for local development
- PostgreSQL through `DATABASE_URL` for production
- Company/workshop, branch and role scoping

## Local setup

Windows:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py makemigrations companies branches roles accounts
python manage.py migrate
python manage.py runserver
```

macOS/Linux:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py makemigrations companies branches roles accounts
python manage.py migrate
python manage.py runserver
```

## First company/admin bootstrap

Set `BOOTSTRAP_COMPANY_NAME`, `BOOTSTRAP_ADMIN_EMAIL`, and `BOOTSTRAP_ADMIN_PASSWORD` in your shell, then run:

```bash
python manage.py bootstrap_cubixgear
```

## Core endpoints

```text
GET  /api/v1/health/

POST /api/v1/auth/login
POST /api/v1/auth/logout
GET  /api/v1/auth/me
POST /api/v1/auth/refresh
POST /api/v1/auth/change-password
POST /api/v1/auth/forgot-password
POST /api/v1/auth/reset-password
POST /api/v1/auth/magic-link
POST /api/v1/auth/magic-link/verify

GET/PATCH          /api/v1/company/me
GET/POST           /api/v1/branches/
GET/PATCH/DELETE   /api/v1/branches/<uuid>
GET/POST           /api/v1/roles/
GET/PATCH/DELETE   /api/v1/roles/<uuid>
```

## Company panel connection

```env
VITE_API_URL=http://127.0.0.1:8000/api/v1
VITE_USE_MOCK_API=false
```

Login returns `token`, `access`, `refresh`, and `user`, matching the current company-panel auth client.
