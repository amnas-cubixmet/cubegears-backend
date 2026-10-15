# CubeGear monthly credit billing emails (opt-in)

This is a **delivery outbox** for monthly feature and storage credit billing.
It does not charge wallets, make Razorpay payments, unlock features or
override backend role permissions.

When a **closed** billing month is settled using either supported engine,
a recipient-specific, deduplicated email notice is saved for active users
who hold `billing.edit` in the company. Events:

- `paid`: credits successfully deducted from the wallet
- `insufficient`: credits still due, or the monthly feature suspended

Notices use the server-computed credit amount, period and company. Staff
without company billing authority are not emailed. No email credentials,
reset tokens or webhook secrets enter the outbox.

## Configuration

Default flags are **OFF**. On a tested worker deployment:

```dotenv
CREDIT_BILLING_CELERY_ENABLED=true
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
EMAIL_CELERY_ENABLED=true
BILLING_EMAIL_ALERTS_ENABLED=true
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=your-smtp-host
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=your-account
EMAIL_HOST_PASSWORD=your-secret
DEFAULT_FROM_EMAIL=CubeGear <billing@your-domain.example>
```

Provide real SMTP values privately in `.env`; never commit them to GitHub.
The outbox Celery task runs every **10 minutes** when all three Celery/email
flags are enabled. It retries temporary SMTP errors with bounded backoff.
No email is queued when billing alert flag is disabled.

```powershell
python manage.py migrate
python manage.py test apps.credits.test_billing_email_notices --noinput
celery -A config.celery:app worker --loglevel=info --pool=solo
# A different terminal, one scheduler only:
celery -A config.celery:app beat --loglevel=info
```

Do not enable automatic billing merely to enable email. The billing
scheduler, SMTP and outbox are independently controlled; only already
settled usage creates notices. An actual worker and Beat are required
for automatic email sending, not the Django web server alone.

SMTP is **at least once**: if a worker crashes after SMTP accepts mail
but before status is persisted, a retry may send a duplicate. This is
a standard unavoidable boundary without provider-idempotency support.
The database reference prevents duplicate *queued* notices for the
same company, admin, feature, month and outcome. A `paid` receipt and
an `insufficient` notice are distinct outcomes.

Failures are recorded as `failed` with retry counters/next retry time;
max six attempts. Review stuck notices. Avoid treating accepted SMTP
delivery as proof the recipient opened/read the email.

## Deployment sequence

1. Backup existing wallet/invoice data.
2. Run `python manage.py check`, migration checks and relevant tests.
3. Apply `0009_billing_email_outbox` without deleting any historic billing.
4. Configure private SMTP credentials; validate with
   `python manage.py check_email_delivery`.
5. Enable alert flags, start one Celery worker and one Beat.
6. Monitor outbox status and legitimate monthly invoice/ledger transitions.
