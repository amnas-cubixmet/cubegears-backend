# Customer Website — Vercel custom domains

## Separate Vercel project

The Customer Website is **not** the Company Panel or the marketing Public Website. Attach workshop custom domains only to the dedicated `cubegears-customers-website` Vercel project.

Environment on **Django backend** (secret settings, not Git source or browser):

```dotenv
VERCEL_CUSTOMER_DOMAIN_TOKEN=<server-only Vercel token scoped for the Customer Website project and team>
VERCEL_CUSTOMER_PROJECT_ID=prj_5f7x9VCbgdpMXhm8UzScO04ojp82
VERCEL_CUSTOMER_TEAM_ID=team_SMV9KJgT1IuTuSjLxvY0a4dn
CUSTOMER_PORTAL_ROOT_DOMAIN=cubegears.com
# Turn on only after default wildcard DNS, Vercel and HTTPS all work:
CUSTOMER_WEBSITE_LINKS_ENABLED=false
# Optional: require CubeGears platform superadmin approval first.
CUSTOMER_DOMAIN_REQUIRE_APPROVAL=true
```

The project/team IDs above were observed in the linked Vercel account on Nov 5, 2026. Verify IDs for the actual production workspace before deploying. Do not put the Vercel token in `VITE_`, source, a frontend environment variable or a publicly readable runtime response. Restrict token permissions to project domain management and rotate it when no longer necessary.

On **Vercel Customer Website project**, configure `CUBEGEARS_BACKEND_URL` to production Django HTTPS URL and `PORTAL_PROXY_SHARED_SECRET` to the same strong (32+ char) server-only secret configured on Django. Configure `CUSTOMER_PORTAL_SIGNING_KEY` securely in Django as usual.

## API flow

Workshop owner with `company.manage`:
1. `POST /api/v1/company/customer-website/domains` with `hostname` and `dnsProvider`. Creates a pending assignment unique across tenants, then attempts to add it to Vercel Customer Website project.
2. `GET /api/v1/company/customer-website/domains` lists the user's own records and Vercel's returned DNS/TXT challenges. `GET /.../<id>` includes history.
3. Configure the returned DNS records with GoDaddy, Cloudflare, Hostinger, Namecheap, Vercel DNS or another authoritative provider. DNS changes are **not** automated.
4. `POST /.../<id>/verify` asks Vercel to verify ownership (when needed) and refreshes authoritative DNS and HTTPS checks. `POST /.../<id>/refresh` rechecks only.
5. The domain is **active** only when Vercel reports owner verification true, `misconfigured=false`, and a valid CA-issued certificate is served over HTTPS. TLS connections reject non-global DNS addresses.
6. `POST /.../<id>/primary` rechecks and promotes it; old primary is only replaced after new domain is ready.
7. `DELETE /.../<id>` disables locally and tries to remove the Vercel project assignment. If external removal fails, domain is *still disabled for tenant routing* and the error stays visible for manual remediation.

Platform-only:
- `GET /api/v1/company/platform/customer-domains` lists tenant domains for platform superuser accounts with no tenant.
- `POST /api/v1/company/platform/customer-domains/<id>/review` with action `approve`, `disable` or `refresh`.
- Django Admin's CompanyDomain and CompanyDomainAudit sections provide readonly status/history and auditable actions.

The existing signed customer-website proxy maps only approved active verified hostnames to their company. The default `<company.slug>.cubegears.com` remains available independently; old links and customer data are never rewritten by changing a domain.

## Wildcard subdomains

Provision `*.cubegears.com` on the **Customer Website** Vercel project; it must not be assigned to Company Panel. Vercel currently requires delegated Vercel DNS nameservers for wildcard certificates. Use the exact Vercel nameservers provided to your account/domain. Changing the authoritative nameservers is a high-impact migration: copy existing MX/SPF/DKIM/DMARC/TXT/CAA records first and verify email is unaffected. Do **not** switch `CUSTOMER_WEBSITE_LINKS_ENABLED=true` until DNS and HTTPS are live and verified.

## Operational notes

- The DNS checks run on workshop admin actions (not background jobs).
- Domain uniqueness is enforced at the database level even after deactivation.
- A provider outage must not activate pending domains.
- Changing the primary hostname does not migrate browser cookies between different eTLD+1 domains. Customers should reopen a freshly issued/retrieved magic link for the new domain. Existing tokens remain revocable.
- Actual DNS zone changes, public propagation, Vercel SSL issuance, and end-to-end magic-link routing require staging/live integration testing after deployment.
- Initial Vercel project deployment may be blocked by Hobby plan rate limits; do not treat successful Git linking as proof of a live site.

## CI

```bash
python manage.py makemigrations --check --dry-run
python manage.py migrate
python manage.py test apps.companies.tests_vercel_domains apps.customers.tests_website
```

## Default workshop websites: one wildcard, no per-workshop Vercel setup

This is a **single Next.js multi-tenant Customer Website**, not one Vercel
project per workshop. The signup backend reserves one unique public DNS label
in `CompanyWebsiteSubdomain`. It does not create Vercel projects or add a
project-domain entry for every workshop.

1. Choose and **own** the platform root domain (for example `cubegears.com`
   or any different registered domain). The root is configured on Django with
   `CUSTOMER_PORTAL_ROOT_DOMAIN`. Example names do not imply ownership.
2. In Vercel, add `*.YOUR-REGISTERED-DOMAIN` **once** to the dedicated
   `cubegears-customers-website` project. Wildcard certificates require the
   domain to use Vercel nameservers. Preserve/migrate the existing mail and
   other DNS records before changing authoritative nameservers.
3. Confirm wildcard ownership, DNS resolution, production deployment,
   certificate, and signed Customer Website → Django proxy connectivity.
   Configure the Customer Website server-only `CUBEGEARS_BACKEND_URL` and
   `PORTAL_PROXY_SHARED_SECRET`. Django must use the same proxy secret.
4. Only after live verification, set `CUSTOMER_WEBSITE_LINKS_ENABLED=true`
   on Django to enable issuing default subdomain tracking links.
5. New workshop "ABCD" automatically gets an available address such as
   `abcd.YOUR-REGISTERED-DOMAIN`. If that label is taken, signup assigns
   a unique suffixed label. The owner can change their public label using
   `PATCH /api/v1/company/customer-website/subdomain` (`{"label":"new-garage"}`)
   or the Company Panel Customer Website settings. This does not edit the
   company's `slug` used for staff login or data access.
6. The customer can optionally add their **own** domain, such as
   `portal.mygarage.in`, through Customer Website → Domains. Backend
   adds this to the same Vercel Customer Website project using the
   server-only Vercel token, displays Vercel-provided DNS/TXT records
   in the UI, and activates it only after ownership/DNS/HTTPS checks.
   The owner adds those DNS records at their registrar. The default workshop
   subdomain remains available and is the fallback if no custom domain exists.

To test separately on localhost in development, use
`http://abcd.localhost:3000` (or the local Next.js port) with Django DEBUG and
the signed Customer Website BFF. Use `PORTAL_BACKEND_URL` to route to Django on
`http://127.0.0.1:8000`, plus the shared local secret. Some devices and DNS
resolvers do not resolve `*.localhost` automatically; use a supported local
DNS/hosts mapping if required. Never rely on local wildcard DNS as evidence of
live Vercel configuration.

**Operational security:** A changed default label stops resolving at the old
hostname; resend customer access links. Existing customer tokens remain bound
to their company/customer, not to the hostname, and are still individually
revocable. Custom domains require company/platform approval checks as before.
Platform-level registration/DNS changes happen once; owners cannot buy, claim,
or modify the platform root through the company website settings.
