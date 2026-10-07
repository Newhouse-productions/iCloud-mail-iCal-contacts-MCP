"""WebDAV client safety: redirects, URL containment, relative hrefs."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from icloud_mail_mcp import dav


@pytest.mark.parametrize(
    "target, allowed",
    [
        ("https://p42-caldav.icloud.com/123/", True),
        ("http://p42-caldav.icloud.com/123/", False),  # credentials over plain HTTP
        ("https://evil.example.com/", False),  # another site
        ("https://icloud.com.evil.example/", False),
    ],
)
def test_redirects_stay_on_https_and_same_site(target, allowed):
    assert dav._safe_redirect("https://caldav.icloud.com/", target) is allowed


def test_child_of():
    cal = "https://p1-caldav.icloud.com/1/calendars/home/"
    assert dav.child_of(cal, cal + "abc.ics")
    for bad in (cal, cal + "../work/a.ics", cal + "..%2Fwork%2Fa.ics", cal + "a/b.ics", cal + "..", "https://x/a.ics"):
        assert not dav.child_of(cal, bad)


def test_refused_redirect_is_an_error(monkeypatch):
    client = dav.DavClient("https://caldav.icloud.com/", "me@icloud.com", "pw", 5, "iCloud Calendar")

    def redirect(method, url, body, headers, timeout):
        return dav.Reply(301, "Moved", {"location": "http://caldav.icloud.com/"}, b"")

    monkeypatch.setattr(dav.HTTP, "send", redirect)
    with pytest.raises(ToolError, match="refusing to follow a redirect"):
        client.request("PROPFIND", "/")


def test_relative_hrefs_resolve_against_final_url(monkeypatch):
    client = dav.DavClient("https://caldav.icloud.com/", "me@icloud.com", "pw", 5, "iCloud Calendar")
    body = (
        b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>/123/calendars/home/</d:href>'
        b"<d:propstat><d:prop><d:displayname>Home</d:displayname></d:prop>"
        b"<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"
    )
    monkeypatch.setattr(client, "request", lambda *a, **k: (207, body, "https://p42-caldav.icloud.com/123/"))
    [resp] = client.propfind("/", ["d:displayname"])
    assert resp.href == "https://p42-caldav.icloud.com/123/calendars/home/"
    assert resp.text("d:displayname") == "Home"


@pytest.mark.parametrize(
    "target, allowed",
    [
        ("https://p42-caldav.icloud.com/1/", True),
        ("https://contacts.icloud.com/", True),
        ("https://evil-icloud.com/", False),
        ("https://icloud.com.evil.example/", False),
    ],
)
def test_icloud_may_only_redirect_within_icloud(target, allowed):
    assert dav._safe_redirect("https://caldav.icloud.com/", target) is allowed


@pytest.mark.parametrize(
    "target, allowed",
    [
        ("https://dav.example.co.uk/x/", True),
        ("https://other.example.co.uk/x/", False),  # a "same last two labels" rule would allow this
        ("https://dav.example.co.uk:8443/x/", False),
    ],
)
def test_other_servers_may_only_redirect_to_themselves(target, allowed):
    assert dav._safe_redirect("https://dav.example.co.uk/", target) is allowed


@pytest.mark.parametrize(
    "url, ok",
    [
        ("https://caldav.icloud.com/", True),
        ("http://127.0.0.1:5232/", True),
        ("http://localhost/", True),
        ("http://caldav.example.com/", False),
        ("ftp://caldav.icloud.com/", False),
    ],
)
def test_plain_http_is_refused_except_locally(url, ok):
    if ok:
        dav.require_https(url, "ICLOUD_CALDAV_URL")
    else:
        with pytest.raises(ToolError, match="https"):
            dav.require_https(url, "ICLOUD_CALDAV_URL")


def test_calendar_client_refuses_http_url():
    from conftest import configure

    from icloud_mail_mcp import calendars

    configure(caldav_url="http://caldav.example.com/")
    with pytest.raises(ToolError, match="ICLOUD_CALDAV_URL must be an https"):
        calendars.list_calendars()


@pytest.mark.parametrize(
    "url",
    [
        "http://caldav.icloud.com/123/principal/",  # a server-supplied link downgraded to HTTP
        "https://evil.example.com/123/",  # or pointing elsewhere
    ],
)
def test_credentials_never_follow_a_bad_server_link(monkeypatch, url):
    sent = []
    monkeypatch.setattr(dav.HTTP, "send", lambda *a, **k: sent.append(a) or dav.Reply(207, "OK", {}, b""))
    client = dav.DavClient("https://caldav.icloud.com/", "me@icloud.com", "pw", 5, "iCloud Calendar")
    with pytest.raises(ToolError, match="refusing to send your credentials"):
        client.request("PROPFIND", url)
    assert sent == []


def test_writes_never_reuse_or_retry_a_connection(monkeypatch):
    borrowed = []
    monkeypatch.setattr(dav.HTTP, "_borrow", lambda key: borrowed.append(key))
    monkeypatch.setattr(dav.HTTP, "_connect", lambda *a: (_ for _ in ()).throw(ConnectionResetError("reset")))
    with pytest.raises(ConnectionResetError):
        dav.HTTP.send("PUT", "https://caldav.icloud.com/x.ics", b"x", {}, 5)
    assert borrowed == []  # a fresh connection, and the failure isn't retried


def test_portless_proxy_uses_urllibs_default(monkeypatch):
    import os

    for name in list(os.environ):
        if name.lower().endswith("_proxy"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("https_proxy", "http://proxy.corp")
    assert dav._proxy_for("https", "caldav.icloud.com")[:2] == ("proxy.corp", 443)
