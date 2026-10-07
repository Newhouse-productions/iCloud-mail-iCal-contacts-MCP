"""Keep-alive connections for CalDAV/CardDAV: reuse, stale-connection retry, proxies."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from icloud_mail_mcp import dav

MULTISTATUS = (
    b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>/cal/</d:href><d:propstat><d:prop>'
    b"<d:displayname>Home</d:displayname></d:prop><d:status>HTTP/1.1 200 OK</d:status>"
    b"</d:propstat></d:response></d:multistatus>"
)


class KeepAliveHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep connections open, like iCloud
    connections: list = []
    drop_after_response = False

    def do_PROPFIND(self):
        # Decide before responding: once the client has the response it may change the flag
        # before this thread reads it (a race that made CI flaky).
        drop = type(self).drop_after_response
        type(self).connections.append(self.client_address)
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(207)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(MULTISTATUS)))
        self.end_headers()
        self.wfile.write(MULTISTATUS)
        if drop:  # the server silently closes the idle connection
            self.close_connection = True

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    KeepAliveHandler.connections = []
    KeepAliveHandler.drop_after_response = False
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), KeepAliveHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()
    httpd.server_close()


def test_connections_are_reused_across_clients(server):
    before = dav.HTTP.opened
    for _ in range(5):  # a new DavClient per tool call, as the tools create them
        client = dav.DavClient(server, "me@icloud.com", "pw", 5, "test")
        assert client.propfind("/", ["d:displayname"])[0].text("d:displayname") == "Home"
    assert dav.HTTP.opened - before == 1
    assert len({addr for addr in KeepAliveHandler.connections}) == 1


def test_a_connection_the_server_closed_is_retried_once(server):
    client = dav.DavClient(server, "me@icloud.com", "pw", 5, "test")
    KeepAliveHandler.drop_after_response = True  # closes without saying "Connection: close"
    client.propfind("/", ["d:displayname"])
    before = dav.HTTP.opened
    KeepAliveHandler.drop_after_response = False
    assert client.propfind("/", ["d:displayname"])[0].text("d:displayname") == "Home"
    assert dav.HTTP.opened - before == 1  # the dead connection was replaced, once


def test_stale_reused_connection_is_replaced(server, monkeypatch):
    client = dav.DavClient(server, "me@icloud.com", "pw", 5, "test")
    client.propfind("/", ["d:displayname"])
    import socket

    [(conn, _)] = next(iter(dav.HTTP._idle.values()))
    conn.sock.shutdown(socket.SHUT_RDWR)  # the connection is gone, as when a server drops it
    assert client.propfind("/", ["d:displayname"])[0].text("d:displayname") == "Home"


def test_failure_on_a_new_connection_is_not_retried(monkeypatch):
    client = dav.DavClient("http://127.0.0.1:9/", "me@icloud.com", "pw", 2, "iCloud Calendar")
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="Could not reach iCloud Calendar"):
        client.propfind("/", ["d:displayname"])


def test_proxy_routing(monkeypatch):
    import os

    for name in list(os.environ):
        if name.lower().endswith("_proxy"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("https_proxy", "http://user:p%40ss@proxy.example:3128")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    host, port, auth = dav._proxy_for("https", "caldav.icloud.com")
    assert (host, port) == ("proxy.example", 3128) and auth.startswith("Basic ")
    import base64

    assert base64.b64decode(auth.split()[1]) == b"user:p@ss"
    assert dav._proxy_for("https", "localhost") is None
    assert dav._proxy_for("http", "caldav.icloud.com") is None
