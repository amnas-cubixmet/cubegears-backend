# Customer Website — default workshop subdomains only

The Customer Website is one secure, multi-tenant Next.js app, separate from
the Company Panel and public marketing website. Every workshop gets one unique
default address: `<workshop-label>.<CUSTOMER_PORTAL_ROOT_DOMAIN>`.

## Production deployment

1. Own and configure the platform `CUSTOMER_PORTAL_ROOT_DOMAIN` (do not treat
   example domains as owned or configured). Provision a wildcard DNS/HTTPS
   certificate on the Customer Website deployment.
2. Configure `CUSTOMER_WEBSITE_LINKS_ENABLED=true` **only after** the wildcard
   hostname, HTTPS certificate, and proxy-to-Django flow are verified live.
3. Configure `CUBEGEARS_BACKEND_URL` and a 32+ character server-only
   `PORTAL_PROXY_SHARED_SECRET` on the Next.js deployment. Django must have
   the identical `PORTAL_PROXY_SHARED_SECRET` and its own
   `CUSTOMER_PORTAL_SIGNING_KEY`.
4. Registration reserves a `CompanyWebsiteSubdomain` record. Owners can edit
   that label through `PATCH /api/v1/company/customer-website/subdomain`.
   Changing the label requires sending fresh access links; it does not change
   the Company's staff authentication slug or customer data.
5. Development uses signed `<label>.localhost:<configured port>` hostnames
   and a local backend; development ports should come from environment config,
   not a hard-coded production URL.

## Security

Only signed, validated portal proxy hostnames resolve to an active workshop.
Production routing only resolves registered labels on the configured platform
root. Unknown, forged, unregistered, or arbitrary external hostnames return
404. The customer session remains bound to its company and customer, and
customer access links are revocable.

Per-workshop custom domains, Vercel per-domain project registration, DNS/TXT
verification, and external domain approval endpoints have been retired.
Existing historical Django migrations remain so older databases can migrate
forward. The `0010_remove_custom_domains` migration drops the retired
custom-domain tables; take a database backup before applying it to production.
