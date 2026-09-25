from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from .model import validate_payload, utcnow


class RemoteError(Exception):
    def __init__(self, reason: str, retry_after: float = 0):
        super().__init__(reason)
        self.retry_after = retry_after


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
        try:
            for _ in range(6):
                if self._origin(url) != self.origin or urlsplit(url).username or urlsplit(url).password:
                    raise RemoteError("unsafe_redirect")
                with self.client.stream(method, url, **kwargs) as response:
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.cfg["max_response_bytes"]:
                            raise RemoteError("response_too_large")
                    result = httpx.Response(response.status_code, headers=response.headers,
                                            content=bytes(body), request=response.request)
                if result.status_code == 429:
                    raise RemoteError("rate_limited", retry_after(result.headers.get("Retry-After"), utcnow()))
                if result.status_code >= 500:
                    raise RemoteError("server_error")
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
        except httpx.HTTPError:
            raise RemoteError("network_error") from None

    @staticmethod
    def check(response):
        form = LoginForm(response.text).login() if "<" in response.text[:1000] else None
        if response.status_code == 401 or form:
            raise LoggedOut("session_lost")
        if response.status_code == 403:
            raise Forbidden("forbidden_unclassified")
        if response.status_code != 200:
            raise RemoteError("unexpected_status")

    def login(self):
        self.client.cookies.clear()
        response = self.request("GET", "/sign/in/")
        if response.status_code != 200:
            raise RemoteError("login_form_unavailable")
        form = LoginForm(response.text).login()
        if not form:
            raise RemoteError("login_form_not_found")
        fields = dict(form["fields"], username=self.username, password=self.password,
                      send="Přihlásit", _do="signInForm-submit")
        response = self.request("POST", urljoin("/sign/in/", form["action"]), data=fields)
        # These explicit messages are conservative; real error wording must be verified on site.
        lower = response.text.casefold()
        if any(text in lower for text in ("nesprávné uživatelské jméno nebo heslo", "invalid username or password", "neplatné přihlašovací údaje")):
            raise InvalidCredentials("invalid_credentials")
        if response.status_code >= 400 or LoginForm(response.text).login():
            raise RemoteError("login_not_confirmed")
        # A redirect alone never authenticates a session.
        try:
            return self.read()
        except LoggedOut:
            raise RemoteError("login_not_confirmed") from None

    def read(self):
        response = self.request("POST", "/api/json/reply/DeviceSignal", json={"DeviceID": self.cfg["device_id"]})
        received_at = utcnow()
        self.check(response)
        try:
            return validate_payload(response.json()), received_at
        except (ValueError, TypeError):
            raise RemoteError("invalid_payload") from None

    def reload(self):
        response = self.request("POST", "/api/json/reply/DeviceSignalReload", json={"DeviceID": self.cfg["device_id"]})
        if response.status_code != 204:
            self.check(response)
            raise RemoteError("unexpected_reload_status")

    def close(self):
        self.client.close()

