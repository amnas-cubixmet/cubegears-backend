"""Server-only Vercel project-domain integration: no client-held token or guessed DNS."""
import ipaddress
import json
import os
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.utils import timezone
from .models import CompanyDomain, CompanyDomainAudit


class VercelDomainError(Exception):
    def __init__(self, message, code=502):
        super().__init__(str(message)[:500])
        self.code = code


def domain_settings():
    return {
        "token": os.environ.get("VERCEL_CUSTOMER_DOMAIN_TOKEN", "").strip(),
        "project": os.environ.get("VERCEL_CUSTOMER_PROJECT_ID", "").strip(),
        "team": os.environ.get("VERCEL_CUSTOMER_TEAM_ID", "").strip(),
    }


class VercelDomains:
    def __init__(self):
        options = domain_settings()
        self.token = options["token"]
        self.project = options["project"]
        self.team = options["team"]
        # Never fall back to the Company Panel/Public Site project.
        if not self.token or not self.project.startswith("prj_") or not self.team.startswith("team_"):
            raise VercelDomainError(
                "Customer Website Vercel credentials/project/team are not configured.", 503
            )

    def request(self, method, path, data=None):
        query = urlencode({"teamId": self.team})
        url = "https://api.vercel.com" + path + ("&" if "?" in path else "?") + query
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode()
        req = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=10) as response:
                raw = response.read(131072)
                return json.loads(raw) if raw.strip() else {}
        except HTTPError as exc:
            try:
                error = json.loads(exc.read(8192)).get("error", {})
            except (ValueError, TypeError):
                error = {}
            message = error.get("message") if isinstance(error, dict) else None
            if exc.code in (401, 403):
                message = "Vercel project access denied. Check server token and team permissions."
            elif exc.code == 409:
                message = message or "Domain is already associated with another Vercel project."
            else:
                message = message or "Vercel could not process this domain request."
            raise VercelDomainError(message, exc.code) from None
        except (URLError, OSError, ValueError) as exc:
            raise VercelDomainError("Cannot reach the Vercel domain service; try again.") from exc

    def path(self, hostname):
        return "/v9/projects/" + quote(self.project, safe="") + "/domains/" + quote(hostname, safe="")

    def add(self, hostname):
        try:
            result = self.request(
                "POST", "/v10/projects/" + quote(self.project, safe="") + "/domains",
                {"name": hostname},
            )
        except VercelDomainError as exc:
            if exc.code not in (400, 409):
                raise
            # A previous add may have succeeded before a network timeout.
            # Retry only after verifying the exact same project and domain;
            # never force-reassign a domain from another Vercel project.
            try:
                return self.detail(hostname)
            except VercelDomainError:
                raise exc from None
        if result.get("projectId") != self.project or result.get("name", "").lower() != hostname:
            raise VercelDomainError("Vercel returned an unexpected project/domain assignment.")
        return result

    def detail(self, hostname):
        result = self.request("GET", self.path(hostname))
        if result.get("projectId") != self.project or result.get("name", "").lower() != hostname:
            raise VercelDomainError("This hostname is not assigned to the configured Customer Website project.", 409)
        # Redirecting or branch-preview domains must never expose customer records.
        if result.get("redirect") or result.get("gitBranch") or result.get("customEnvironmentId"):
            raise VercelDomainError("Only a production Customer Website domain can be activated.", 409)
        return result

    def configuration(self, hostname):
        return self.request("GET", "/v6/domains/" + quote(hostname, safe="") + "/config?projectIdOrName=" + quote(self.project, safe=""))

    def verify(self, hostname):
        return self.request("POST", self.path(hostname) + "/verify")

    def remove(self, hostname):
        return self.request("DELETE", self.path(hostname))


def _relative_name(record_host, apex):
    record_host = str(record_host or "").strip(".").lower()
    apex = str(apex or "").strip(".").lower()
    if record_host == apex:
        return "@"
    if apex and record_host.endswith("." + apex):
        return record_host[:-(len(apex) + 1)]
    # Vercel may return a different challenge hostname; preserve it exactly.
    return record_host


def _recommendation(items):
    # Current Vercel config schema: [{rank:1, value:string|string[]}].
    if not isinstance(items, list):
        return []
    records = sorted(
        (x for x in items if isinstance(x, dict) and isinstance(x.get("rank"), (int, float))),
        key=lambda x: x["rank"],
    )
    for record in records:
        value = record.get("value")
        values = value if isinstance(value, list) else [value]
        vals = [str(v).strip() for v in values if isinstance(v, str) and v.strip()]
        if vals:
            return vals
    return []


def dns_requirements(hostname, details, config):
    apex = str(details.get("apexName", "")).strip(".").lower()
    if not apex:
        raise VercelDomainError("Vercel has not provided the domain DNS zone.")
    records = []
    if hostname == apex:
        for value in _recommendation(config.get("recommendedIPv4")):
            records.append({"type": "A", "name": "@", "value": value})
    else:
        for value in _recommendation(config.get("recommendedCNAME"))[:1]:
            records.append({
                "type": "CNAME", "name": _relative_name(hostname, apex),
                "value": value,
            })
    challenges = []
    for challenge in details.get("verification") or []:
        if not isinstance(challenge, dict):
            continue
        kind = challenge.get("type")
        name, value = challenge.get("domain"), challenge.get("value")
        if kind == "TXT" and isinstance(name, str) and isinstance(value, str):
            challenges.append({
                "type": "TXT", "name": _relative_name(name, apex),
                "value": value,
            })
    return records, challenges


def certificate_ready(hostname):
    """Probe real, CA-validated TLS, not Vercel's 'can issue a cert' flag.

    Resolve publicly routable addresses only; never connect to private/loopback
    addresses derived from an untrusted hostname (SSRF hardening).
    """
    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        for _, _, _, _, addr in addresses:
            ip = ipaddress.ip_address(addr[0])
            if not ip.is_global:
                return False
        context = ssl.create_default_context()
        for family, socktype, proto, _, addr in addresses[:4]:
            try:
                with socket.socket(family, socktype, proto) as connection:
                    connection.settimeout(4)
                    connection.connect(addr)
                    with context.wrap_socket(connection, server_hostname=hostname) as tls:
                        if tls.getpeercert():
                            return True
            except (OSError, ssl.SSLError):
                continue
    except (OSError, ValueError):
        return False
    return False


def record_domain_event(domain, actor, action, details=None):
    CompanyDomainAudit.objects.create(
        domain=domain, company=domain.company, actor=actor,
        action=action, details=details or {},
    )


def refresh_domain(domain, actor=None, verify=False, attach=False):
    """Refresh one tenant-owned domain using Vercel as the source of truth.

    A stale ACTIVE row is immediately demoted if DNS, assignment or TLS fails.
    A provider outage does not promote any inactive domain.
    """
    if domain.status == CompanyDomain.DISABLED or not domain.is_approved:
        raise VercelDomainError("Domain is disabled by the platform.", 403)
    previous = domain.status
    client = VercelDomains()
    try:
        if attach:
            client.add(domain.hostname)
        details = client.detail(domain.hostname)
        if verify and details.get("verified") is False:
            client.verify(domain.hostname)
            details = client.detail(domain.hostname)
        config = client.configuration(domain.hostname)
        records, txt = dns_requirements(domain.hostname, details, config)
        confirmed = details.get("verified") is True
        dns_ok = config.get("misconfigured") is False
        # Missing Vercel recommendations must not invent an A/CNAME.
        if not confirmed:
            next_status = CompanyDomain.PENDING_VERIFICATION
        elif not dns_ok:
            next_status = CompanyDomain.PENDING_DNS
        else:
            next_status = CompanyDomain.SSL_PENDING
        tls_ok = bool(confirmed and dns_ok and certificate_ready(domain.hostname))
        if tls_ok:
            next_status = CompanyDomain.ACTIVE
        domain.status = next_status
        domain.dns_records = records
        domain.verification_records = txt
        domain.dns_configured = dns_ok
        domain.ownership_verified = confirmed
        domain.ssl_status = "ready" if tls_ok else "pending"
        domain.verified_at = timezone.now() if tls_ok else None
        domain.last_checked_at = timezone.now()
        domain.last_error = ""
        domain.vercel_project_id = client.project
        if previous == CompanyDomain.ACTIVE and next_status != CompanyDomain.ACTIVE:
            domain.is_primary = False
        domain.save()
        record_domain_event(domain, actor, "verified" if verify else "checked", {
            "previousStatus": previous, "status": next_status,
            "dnsConfigured": dns_ok, "ownershipVerified": confirmed, "sslReady": tls_ok,
        })
    except VercelDomainError as exc:
        domain.status = CompanyDomain.PENDING_SETUP if attach else CompanyDomain.FAILED
        domain.last_error = str(exc)[:500]
        domain.last_checked_at = timezone.now()
        # Fail closed: a failed check may not retain stale active routing.
        domain.verified_at = None
        domain.ssl_status = "pending"
        domain.ownership_verified = False
        domain.dns_configured = False
        domain.is_primary = False
        domain.save()
        record_domain_event(domain, actor, "check_failed", {"reason": str(exc)[:240]})
        raise
    return domain
