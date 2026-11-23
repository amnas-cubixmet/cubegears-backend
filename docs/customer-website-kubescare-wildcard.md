# CubeGears / kubescare.tech: customer wildcard deployment

This guide covers **public Customer Website** subdomains such as
`abc-mortgage.customer.kubescare.tech`. The separately deployed
**Company Panel** (React/Vite) is the owner/admin dashboard, NOT the Customer Website.

## Existing backend implementation (no per-company Vercel API calls)

- `apps/accounts/views.py` public signup calls `ensure_default_subdomain(company)`.
- `apps/companies/subdomains.py` reserves a unique label in `CompanyWebsiteSubdomain`.
- `GET /api/v1/company/customer-website` returns `subdomain`, `subdomainLabel`
  and `wildcardReady` to a company admin. `PATCH /api/v1/company/customer-website/subdomain`
  changes that public website label, not the staff login/company slug.
- `apps/customers/portal_tokens.py` produces `/access/<token>` links on the
  company hostname only when `CUSTOMER_WEBSITE_LINKS_ENABLED=true`.
- `apps/customers/website_views.py` requires a signed, timestamped hostname
  from the **trusted Customer Website server-side proxy**. An ordinary browser
  cannot safely call these tenant-specific endpoints with self-supplied headers.
  Unknown/inactive tenants return 404.

## Production rollout — order matters

1. **Deploy the actual Customer Website frontend** as a separate Vercel
   project. Its server-side BFF must preserve the incoming hostname and
   HMAC-sign `<unix_timestamp>:<host>` with `PORTAL_PROXY_SHARED_SECRET`,
   sending `X-CG-Portal-Host`, `X-CG-Portal-TS`, and `X-CG-Portal-Sig`
   to Django. The public Customer Website/BFF repository is NOT part of the
   two repositories provided for this review. Do not route customer hostnames
   to the Company Panel or directly to the Django API.
2. In **that Customer Website Vercel project** add the domain
   `*.customer.kubescare.tech` (and the base
   `customer.kubescare.tech` only if serving the fallback `/access` route
   there). Use Vercel's nameserver-based wildcard verification and its exact
   DNS instructions. If `kubescare.tech` uses another DNS provider, plan
   appropriate nameserver delegation for `customer.kubescare.tech` with your
   DNS administrator. Preserve existing API, email, MX, SPF, DKIM and other
   unrelated records. Do not invent A/CNAME/NS targets.
3. Configure the **Django backend private production environment**:
   ```dotenv
   CUSTOMER_PORTAL_ROOT_DOMAIN=customer.kubescare.tech
   CUSTOMER_PORTAL_BASE_URL=https://customer.kubescare.tech/access
   PORTAL_PROXY_SHARED_SECRET=<same-strong-random-secret-as-Customer-Website-server>
   CUSTOMER_WEBSITE_LINKS_ENABLED=False
   ```
   The proxy secret must be at least 32 characters. Set the same value in
   the **Customer Website server-side** deployment, never in Vite `VITE_*`,
   browser JavaScript or Git. `DJANGO_ALLOWED_HOSTS` should still list the
   **Django API hosts** (for example `api.cubixgear.tech`), not the customer
   wildcard, because the wildcard points to Vercel. Keep
   `CORS_ALLOWED_ORIGINS` scoped to approved Company Panel browser origins.
4. Deploy Django with the existing PostgreSQL/Redis/security settings.
   Apply migrations, then run its readiness/health checks. Verify that a
   company signup creates a row in `CompanyWebsiteSubdomain`.
5. Validate DNS, HTTPS and the Customer Website BFF end-to-end using two
   different test companies, for example:
   - `https://abc-mortgage.customer.kubescare.tech/`
   - `https://second-garage.customer.kubescare.tech/`
   Confirm branding is distinct, missing hosts are 404, customer records are
   never shared across companies, and `/access/<token>` works only on the
   correct company host. Direct unsigned Django branding calls SHOULD fail.
6. **Only after** those checks, set
   `CUSTOMER_WEBSITE_LINKS_ENABLED=True` on Django and restart/redeploy the
   backend. This flag is an *operator assertion*, NOT a live DNS/SSL probe;
   the Company Panel `wildcardReady` value only reflects this flag.

## Validate repository code

```bash
python manage.py test apps.companies.tests_default_subdomains
```

Run the full Django suite on your own CI/staging environment before release.
This branch replaces the obsolete `tests_vercel_domains.py` assertions
(which imported removed `CompanyDomain` models) with a retired-route guard.
The unused legacy `apps/companies/vercel_domains.py` module still references
retired models; do not re-enable that module or treat it as a working
custom-domain workflow without a separate design/security review.
The local fake-domain tests have also been aligned to the supported
`<company>.localhost` development-host mechanism.

## What does **not** need to change

- No new Vercel project, API token or per-tenant DNS record on every company signup.
- No new database per company or changes to existing `Company.slug` values.
- No `VITE_CUSTOMER_PORTAL_ROOT_DOMAIN` in Company Panel; Django returns the
  configured public hostname via the authenticated website-settings endpoint.

## Rollback

Set `CUSTOMER_WEBSITE_LINKS_ENABLED=False` before changing DNS. Existing
customer links already issued on wildcard hostnames may need to be resent with
the fallback address. Keep the DNS and Customer Website deployment intact
until the rollback path is verified.
