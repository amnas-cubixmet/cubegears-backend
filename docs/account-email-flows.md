# Account email and password flows

| Account | Creation | Setup/reset destination |
| --- | --- | --- |
| Platform owner / superadmin | Authorized Control Panel user creation | Backend `/login/password-setup/...` or `/login/password-reset/...` |
| Platform admin staff | Authorized platform staff/user creation, with a platform role | Same Control Panel routes |
| Company owner | Public workshop signup | `FRONTEND_URL/setup-password` or `/reset-password` |
| Company staff | Company-authorized staff creation, with an exact company role | Same Company Panel routes |

Account emails are queued after transaction commit and sent by the Celery
worker. Setup invitations go to the created user's email address; plaintext
passwords are never included. Existing accounts get reset or one-use sign-in
links without changing their current password merely by requesting an invite.

`apps.accounts.account_links` chooses the account's correct destination and
normalizes a trailing slash in `FRONTEND_URL`. A platform account requesting
recovery from the Company Panel API still receives a Control Panel link.
Workshop setup/reset endpoints reject platform-account tokens without changing
the account, password, role, or staff/superuser flags. Control Panel setup/reset
keeps those flags and uses its own session login. Successful Control Panel
magic login also marks the email verified.

`apps.accounts.test_account_email_matrix` covers real signup/staff/user creation,
email recipient and route, single-use setup/reset, login with the new password,
rejection of the old password, and unchanged company/role/privilege identity.
It includes platform owner, superadmin, admin staff, company owner and company
staff. The matrix uses a simulated Celery worker and Django test mailbox.
The separate `scripts/smoke_celery_worker.py` verifies real Redis/worker execution
with a temporary file mailbox; CI runs both kinds of checks.

For live inbox delivery, configure the shared backend/worker environment:
`EMAIL_CELERY_ENABLED`, `CELERY_BROKER_URL`, SMTP settings and the public
`FRONTEND_URL`. Run `python manage.py check_celery --json` and
`python manage.py check_email_delivery`; see [worker setup](celery-worker-setup.md).
These checks do not prove SMTP provider acceptance or inbox delivery. No live
mailbox delivery is claimed by the automated test results.
