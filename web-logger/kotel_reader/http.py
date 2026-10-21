from __future__ import annotations

from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
import time
import hashlib
import json

from .model import validate_payload, utcnow


class RemoteError(Exception):
    def __init__(self, reason: str, retry_after: float = 0, details: dict | None = None):
        super().__init__(reason)
        self.retry_after = retry_after
        self.details = details or {}


class LoggedOut(RemoteError):
    pass


class InvalidCredentials(RemoteError):
    pass


class Forbidden(RemoteError):
    pass


class LoginForm(HTMLParser):
    def __init__(self, html: str):
        super().__init__()
        self.forms, self.current = [], None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"action": attrs.get("action", "/sign/in/"), "fields": {}, "password": False}
        if tag == "input" and self.current is not None:
            if attrs.get("type") == "password":
                self.current["password"] = True
            if attrs.get("type") == "hidden" and attrs.get("name"):
                self.current["fields"][attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form" and self.current is not None:
            self.forms.append(self.current)
            self.current = None

    def login(self):
        return next((f for f in self.forms if f["password"] and "_token_" in f["fields"]), None)


def retry_after(value: str | None, now: float) -> float:
    if not value:
        return 60
    try:
        return max(0, float(int(value)))
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return 60


class Thematronic:
    def __init__(self, config: dict, username: str, password: str, transport=None):
        self.cfg, self.username, self.password = config, username, password
        self.origin = self._origin(config["url"])
        self.client = httpx.Client(timeout=config["timeout_s"], follow_redirects=False,
                                   verify=True, transport=transport, trust_env=False)

    @staticmethod
    def _origin(url):
        u = urlsplit(url)
        return u.scheme, u.hostname, u.port or 443

    def request(self, method, path, **kwargs):
        url = urljoin(self.cfg["url"] + "/", path)
        stage = {
            ("GET", "/sign/in/"): "login_form",
            ("POST", "/api/json/reply/DeviceSignal"): "device_signal",
            ("POST", "/api/json/reply/DeviceSignalReload"): "device_reload",
        }.get((method, urlsplit(url).path), "login_submit" if method == "POST" else "request")
        details = {"stage": stage}
        started = time.monotonic()
        try:
            for redirect_count in range(6):
                details = {"stage": stage, "method": method, "redirects": redirect_count}
                if self._origin(url) != self.origin or urlsplit(url).username or urlsplit(url).password:
                    raise RemoteError("unsafe_redirect")
                with self.client.stream(method, url, **kwargs) as response:
                    encoding = response.headers.get("content-encoding", "identity").lower()
                    content_type = response.headers.get("content-type", "").split(";")[0].lower()
                    details.update(http_status=response.status_code,
                                   content_encoding=encoding if encoding in ("identity", "gzip", "deflate", "br", "zstd") else "other",
                                   response_kind="html" if content_type == "text/html" else "json" if content_type == "application/json" else "other",
                                   cookie_count=len(self.client.cookies))
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.cfg["max_response_bytes"]:
                            raise RemoteError("response_too_large")
                    # iter_bytes() has ALREADY decompressed the body. Keeping the original
                    # Content-Encoding makes the new Response try to decompress it again.
                    details.update(duration_ms=round((time.monotonic() - started) * 1000), response_bytes=len(body))
                    headers = response.headers.copy()
                    for name in ("content-encoding", "content-length", "transfer-encoding"):
                        headers.pop(name, None)
                    result = httpx.Response(response.status_code, headers=headers,
                                            content=bytes(body), request=response.request,
                                            extensions={"kotel_diagnostics": dict(details)})
                if result.status_code == 429 or result.status_code >= 500:
                    header = result.headers.get("Retry-After")
                    retry = retry_after(header, utcnow()) if header or result.status_code == 429 else 0
                    details.update(retry_after_present=header is not None, retry_after_s=retry,
                                   body_sha256=hashlib.sha256(result.content).hexdigest())
                    # Fixed vocabulary only, never copy arbitrary HTML/header text.
                    text = result.text[:32768].casefold()
                    details["error_page_markers"] = [word for word in
                        ("service unavailable", "bad gateway", "gateway timeout", "maintenance", "nginx", "cloudflare", "varnish", "haproxy")
                        if word in text]
                    server = result.headers.get("Server", "").casefold()
                    details["server_family"] = next((name for name in ("nginx", "cloudflare", "apache", "microsoft-iis", "envoy", "caddy")
                                                    if name in server), "other_or_missing")
                    raise RemoteError("rate_limited" if result.status_code == 429 else "server_error", retry)
                if result.status_code in (301, 302, 303, 307, 308):
                    location = result.headers.get("location")
                    if not location:
                        raise RemoteError("redirect_without_location")
                    url = urljoin(url, location)
                    if result.status_code in (301, 302, 303):
                        method, kwargs = "GET", {}
                    continue
                return result
            raise RemoteError("too_many_redirects")
        except httpx.HTTPError as error:
            # Class names are diagnostic; exception messages may include URLs, tokens or bodies.
            details["duration_ms"] = round((time.monotonic() - started) * 1000)
            details["exception_type"] = type(error).__name__
            reason = "response_decode_error" if isinstance(error, httpx.DecodingError) else "network_error"
            raise RemoteError(reason, details=details) from None
        except RemoteError as error:
            error.details = dict(details, **error.details)
            raise

    @staticmethod
    def check(response):
        details = response.extensions.get("kotel_diagnostics", {})
        form = LoginForm(response.text).login() if "<" in response.text[:1000] else None
        if response.status_code == 401 or form:
            raise LoggedOut("session_lost", details=details)
        if response.status_code == 403:
            raise Forbidden("forbidden_unclassified", details=details)
        if response.status_code != 200:
            raise RemoteError("unexpected_status", details=details)

    def login(self):
        self.client.cookies.clear()
        response = self.request("GET", "/sign/in/")
        details = response.extensions.get("kotel_diagnostics", {})
        if response.status_code != 200:
            raise RemoteError("login_form_unavailable", details=details)
        form = LoginForm(response.text).login()
        if not form:
            raise RemoteError("login_form_not_found", details=details)
        fields = dict(form["fields"], username=self.username, password=self.password,
                      send="Přihlásit", _do="signInForm-submit")
        response = self.request("POST", urljoin("/sign/in/", form["action"]), data=fields)
        details = response.extensions.get("kotel_diagnostics", {})
        # These explicit messages are conservative; real error wording must be verified on site.
        lower = response.text.casefold()
        if any(text in lower for text in ("nesprávné uživatelské jméno nebo heslo", "invalid username or password", "neplatné přihlašovací údaje")):
            raise InvalidCredentials("invalid_credentials", details=details)
        if response.status_code >= 400 or LoginForm(response.text).login():
            raise RemoteError("login_not_confirmed", details=details)
        # A redirect alone never authenticates a session.
        try:
            return self.read()
        except LoggedOut as error:
            raise RemoteError("login_not_confirmed", details=error.details) from None

    def read(self):
        response = self.request("POST", "/api/json/reply/DeviceSignal", json={"DeviceID": self.cfg["device_id"]})
        received_at = utcnow()
        self.check(response)
        details = dict(response.extensions.get("kotel_diagnostics", {}))
        details["body_sha256"] = hashlib.sha256(response.content).hexdigest()
        try:
            payload = response.json()
        except (ValueError, TypeError) as error:
            details["validation_reason"] = "invalid_json"
            if isinstance(error, json.JSONDecodeError):
                details.update(json_line=error.lineno, json_column=error.colno)
            raise RemoteError("invalid_payload", details=details) from None
        try:
            return validate_payload(payload), received_at
        except ValueError as error:
            # Validator messages are fixed identifiers, never interpolated payload text.
            details.update(validation_reason=str(error), payload_type=type(payload).__name__)
            details.update(getattr(error, "details", {}))
            if isinstance(payload, (list, dict)):
                details["payload_count"] = len(payload)
            # Shape only: arbitrary response strings/keys could contain credentials.
            if isinstance(payload, list):
                details["item_types"] = [type(item).__name__ for item in payload[:5]]
            raise RemoteError("invalid_payload", details=details) from None

    def reload(self):
        response = self.request("POST", "/api/json/reply/DeviceSignalReload", json={"DeviceID": self.cfg["device_id"]})
        if response.status_code != 204:
            self.check(response)
            raise RemoteError("unexpected_reload_status", details=response.extensions.get("kotel_diagnostics", {}))

    def close(self):
        self.client.close()
