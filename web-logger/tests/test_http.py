from urllib.parse import parse_qs
import gzip
import zlib
import json

import httpx
import pytest

from kotel_reader.http import Forbidden, InvalidCredentials, LoggedOut, RemoteError, Thematronic, retry_after


FORM = '<form action="/sign/in/"><input type="hidden" name="_token_" value="fresh-token"><input type="hidden" name="extra" value="keep"><input type="password" name="password"></form>'


@pytest.mark.parametrize("encoding,compress", [("gzip", gzip.compress), ("deflate", zlib.compress)])
def test_compressed_login_page_is_decoded_once(cfg, encoding, compress):
    def handler(request):
        return httpx.Response(200, headers={"Content-Encoding": encoding, "Content-Type": "text/html"},
                              stream=httpx.ByteStream(compress(FORM.encode())))
    client = Thematronic(cfg["thematronic"], "u", "p", httpx.MockTransport(handler))
    try:
        response = client.request("GET", "/sign/in/")
        assert response.text == FORM
        assert "content-encoding" not in response.headers
        assert int(response.headers["content-length"]) == len(FORM.encode())
    finally:
        client.close()


def test_compressed_login_and_signal_keep_cookies_and_charset(cfg, raw):
    paths = []
    def handler(request):
        paths.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/sign/in/":
            text, kind = FORM, "text/html; charset=utf-8"
            cookies = [("set-cookie", "PHPSESSID=secret-session; Path=/"), ("set-cookie", "ss-id=secret-id; Path=/")]
        elif request.method == "POST" and request.url.path == "/sign/in/":
            assert "PHPSESSID=secret-session" in request.headers["cookie"]
            assert "ss-id=secret-id" in request.headers["cookie"]
            assert parse_qs(request.content.decode())["_token_"] == ["fresh-token"]
            return httpx.Response(303, headers={"location": "/"})
        elif request.url.path == "/":
            text, kind, cookies = "Přihlášeno", "text/html; charset=utf-8", []
        else:
            text, kind, cookies = json.dumps(raw, ensure_ascii=False), "application/json; charset=utf-8", []
        body = gzip.compress(text.encode("utf-8"))
        return httpx.Response(200, headers=[("Content-Encoding", "gzip"), ("Content-Type", kind),
                                           ("Content-Length", str(len(body))), *cookies], stream=httpx.ByteStream(body))
    client = Thematronic(cfg["thematronic"], "test-user", "test-password", httpx.MockTransport(handler))
    try:
        assert client.login()[0] == raw
        assert paths == [("GET", "/sign/in/"), ("POST", "/sign/in/"), ("GET", "/"), ("POST", "/api/json/reply/DeviceSignal")]
    finally:
        client.close()


def test_decoded_size_limit_is_preserved_for_compressed_response(cfg):
    cfg["thematronic"]["max_response_bytes"] = 100
    body = gzip.compress(b"x" * 2000)
    assert len(body) < 100
    client = Thematronic(cfg["thematronic"], "u", "p", httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(body))))
    try:
        with pytest.raises(RemoteError, match="response_too_large") as error:
            client.request("GET", "/sign/in/")
        assert error.value.details["stage"] == "login_form"
    finally:
        client.close()


def test_corrupt_compression_reports_safe_phase_and_type(cfg):
    client = Thematronic(cfg["thematronic"], "secret-user", "secret-password", httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(b"not gzip data"))))
    try:
        with pytest.raises(RemoteError, match="response_decode_error") as error:
            client.login()
        assert error.value.details["exception_type"] == "DecodingError"
        assert error.value.details["stage"] == "login_form"
        assert error.value.details["http_status"] == 200
        assert "secret" not in json.dumps(error.value.details)
        assert "not gzip data" not in json.dumps(error.value.details)
    finally:
        client.close()


def test_login_post_transport_error_identifies_stage_without_secret_values(cfg):
    def handler(r):
        if r.method == "GET":
            return httpx.Response(200, text=FORM)
        raise httpx.ReadTimeout("secret-password URL?token=secret-token")
    client = Thematronic(cfg["thematronic"], "secret-user", "secret-password", httpx.MockTransport(handler))
    try:
        with pytest.raises(RemoteError, match="network_error") as error:
            client.login()
        assert error.value.details.pop("duration_ms") >= 0
        assert error.value.details == {"stage": "login_submit", "method": "POST", "redirects": 0, "exception_type": "ReadTimeout"}
    finally:
        client.close()


def test_login_keeps_all_cookies_and_verifies_signal(cfg, raw):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == "/sign/in/" and request.method == "GET":
            return httpx.Response(200, text=FORM, headers=[("set-cookie", "PHPSESSID=abc; Path=/"), ("set-cookie", "ss-id=xyz; Path=/")])
        assert "PHPSESSID=abc" in request.headers["cookie"]
        assert "ss-id=xyz" in request.headers["cookie"]
        if request.url.path == "/sign/in/":
            fields = parse_qs(request.content.decode())
            assert fields["_token_"] == ["fresh-token"]
            assert fields["extra"] == ["keep"]
            assert fields["password"] == ["test-password"]
            return httpx.Response(303, headers={"location": "/"})
        if request.url.path == "/":
            return httpx.Response(200, text="authorized page")
        return httpx.Response(200, json=raw)
    client = Thematronic(cfg["thematronic"], "test-user", "test-password", httpx.MockTransport(handler))
    assert client.login()[0] == raw
    client.read()
    client.read()
    assert sum(r.url.path == "/sign/in/" and r.method == "POST" for r in calls) == 1
    client.close()


@pytest.mark.parametrize("body,expected", [(FORM, RemoteError), ("Neplatné přihlašovací údaje", InvalidCredentials)])
def test_login_rejects_false_success(cfg, body, expected):
    def handler(r):
        if r.method == "GET" and r.url.path == "/sign/in/":
            return httpx.Response(200, text=FORM)
        return httpx.Response(200, text=body)
    client = Thematronic(cfg["thematronic"], "u", "p", httpx.MockTransport(handler))
    with pytest.raises(expected):
        client.login()
    client.close()


@pytest.mark.parametrize("code,body,error", [(401, "", LoggedOut), (403, "denied", Forbidden),
    (403, FORM, LoggedOut), (200, FORM, LoggedOut), (500, "oops", RemoteError), (200, "{bad", RemoteError), (200, "<html>error</html>", RemoteError)])
def test_read_classification(cfg, code, body, error):
    client = Thematronic(cfg["thematronic"], "u", "p", httpx.MockTransport(lambda r: httpx.Response(code, text=body)))
    with pytest.raises(error):
        client.read()
    client.close()


@pytest.mark.parametrize("location", ["https://evil.example/", "http://jbr1.thematronic.cz/", "https://u:p@jbr1.thematronic.cz/"])
def test_redirect_never_leaks_cookies_or_password(cfg, location):
    requests = []
    def handler(r):
        requests.append(r)
        return httpx.Response(307, headers={"location": location})
    client = Thematronic(cfg["thematronic"], "u", "secret", httpx.MockTransport(handler))
    with pytest.raises(RemoteError, match="unsafe_redirect"):
        client.read()
    assert len(requests) == 1
    client.close()


def test_limit_and_timeout(cfg):
    cfg["thematronic"]["max_response_bytes"] = 10
    client = Thematronic(cfg["thematronic"], "u", "p", httpx.MockTransport(lambda r: httpx.Response(200, text="x" * 11)))
    with pytest.raises(RemoteError, match="response_too_large"):
        client.read()
    client.close()
    def timeout(r):
        raise httpx.ReadTimeout("secret-containing-url")
    client = Thematronic(cfg["thematronic"], "u", "p", httpx.MockTransport(timeout))
    with pytest.raises(RemoteError, match="^network_error$"):
        client.read()
    client.close()


def test_retry_after():
    assert retry_after("120", 0) == 120
    assert retry_after("Thu, 01 Jan 1970 00:02:00 GMT", 30) == 90
    assert retry_after("bad", 0) == 60
    assert retry_after("-1", 0) == 0


def test_rate_limit(cfg):
    client = Thematronic(cfg["thematronic"], "u", "p", httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "600"})))
    with pytest.raises(RemoteError) as error:
        client.read()
    assert error.value.retry_after == 600
    client.close()
