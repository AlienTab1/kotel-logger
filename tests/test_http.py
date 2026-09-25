from urllib.parse import parse_qs

import httpx
import pytest

from kotel_reader.http import Forbidden, InvalidCredentials, LoggedOut, RemoteError, Thematronic, retry_after


FORM = '<form action="/sign/in/"><input type="hidden" name="_token_" value="fresh-token"><input type="hidden" name="extra" value="keep"><input type="password" name="password"></form>'


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

