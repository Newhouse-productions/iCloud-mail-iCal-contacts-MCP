"""A small WebDAV client for iCloud CalDAV (calendars) and CardDAV (contacts).

Standard library only: TLS certificates are verified, every request has a
timeout, connections are kept alive and reused (urllib opens a new TLS connection
for every request), and redirects are followed for every method but only to safe
destinations.
"""

from __future__ import annotations

import base64
import http.client
import logging
import re
import ssl
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlsplit

from mcp.server.mcpserver.exceptions import ToolError

from . import config
from .cache import TTLCache

log = logging.getLogger("icloud-mail")

NS = {
    "d": "DAV:",
    "c": "urn:ietf:params:xml:ns:caldav",
    "card": "urn:ietf:params:xml:ns:carddav",
    "cs": "http://calendarserver.org/ns/",
    "ical": "http://apple.com/ns/ical/",
}
for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)


def tag(name: str) -> str:
    """'d:href' -> '{DAV:}href'."""
    prefix, local = name.split(":")
    return f"{{{NS[prefix]}}}{local}"


@dataclass
class Response:
    """One <d:response> of a multistatus: its href and the properties that came back 200 OK."""

    href: str
    props: dict[str, ET.Element] = field(default_factory=dict)

    def text(self, name: str) -> str:
        el = self.props.get(tag(name))
        return (el.text or "").strip() if el is not None else ""

    def href_in(self, name: str) -> str | None:
        el = self.props.get(tag(name))
        if el is None:
            return None
        href = el.find(tag("d:href"))
        return href.text.strip() if href is not None and href.text else None

    def has_type(self, name: str) -> bool:
        el = self.props.get(tag("d:resourcetype"))
        return el is not None and el.find(tag(name)) is not None


class DavError(ToolError):
    """A WebDAV request the server refused, with its HTTP status for callers that can recover."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _server_says(body: bytes) -> str:
    """A short, single-line excerpt of a server's error body, to make failures diagnosable."""
    text = " ".join(re.sub(rb"<[^>]+>", b" ", body[:2000]).decode("utf-8", "replace").split())
    return f" Server said: {text[:200]}" if text else ""


@dataclass
class Reply:
    status: int
    reason: str
    headers: dict[str, str]  # lower-case names
    body: bytes


_TLS = ssl.create_default_context()  # verifies certificates and host names; shared so sessions resume
# A request on a reused connection that fails like this never reached the server (it had
# already closed the idle connection), so it's safe to send again on a fresh one.
_STALE = (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError, ConnectionAbortedError)
# Requests that are safe to send twice. Anything else (PUT, DELETE) always gets a fresh
# connection and is never retried: a dropped reply after the server acted would otherwise
# turn a successful create into a misleading "already exists" (or a duplicate).
_RETRYABLE = frozenset({"GET", "HEAD", "OPTIONS", "PROPFIND", "REPORT"})


def _proxy_for(scheme: str, host: str) -> tuple[str, int, str | None] | None:
    """(host, port, Proxy-Authorization) from HTTPS_PROXY/NO_PROXY, as urllib would use."""
    if scheme != "https" or urllib.request.proxy_bypass(host):
        return None
    proxy = urllib.request.getproxies().get("https")
    if not proxy:
        return None
    parts = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
    if not parts.hostname:
        return None
    auth = None
    if parts.username:
        creds = f"{unquote(parts.username)}:{unquote(parts.password or '')}".encode()
        auth = "Basic " + base64.b64encode(creds).decode()
    return parts.hostname, parts.port or 443, auth  # urllib's default for a port-less proxy


class HttpPool:
    """Keep-alive connections per (scheme, host, port), shared by every DavClient and thread."""

    MAX_IDLE_PER_HOST = 4
    IDLE_SECONDS = 45  # servers drop idle keep-alive connections; don't reuse older ones

    def __init__(self) -> None:
        self._idle: dict[tuple[str, str, int], list[tuple[http.client.HTTPConnection, float]]] = {}
        self._lock = threading.Lock()
        self.opened = 0  # connections created, for tests and debug logs

    def _connect(self, scheme: str, host: str, port: int, timeout: float) -> http.client.HTTPConnection:
        self.opened += 1
        if scheme == "http":  # only ever to this machine (see require_https)
            return http.client.HTTPConnection(host, port, timeout=timeout)
        proxy = _proxy_for(scheme, host)
        if proxy is None:
            return http.client.HTTPSConnection(host, port, timeout=timeout, context=_TLS)
        proxy_host, proxy_port, proxy_auth = proxy
        conn = http.client.HTTPSConnection(proxy_host, proxy_port, timeout=timeout, context=_TLS)
        conn.set_tunnel(host, port, headers={"Proxy-Authorization": proxy_auth} if proxy_auth else None)
        return conn

    def _borrow(self, key: tuple[str, str, int]) -> http.client.HTTPConnection | None:
        with self._lock:
            idle = self._idle.get(key, [])
            while idle:
                conn, since = idle.pop()
                if time.monotonic() - since < self.IDLE_SECONDS:
                    return conn
                conn.close()
        return None

    def _give_back(self, key: tuple[str, str, int], conn: http.client.HTTPConnection) -> None:
        with self._lock:
            idle = self._idle.setdefault(key, [])
            if len(idle) < self.MAX_IDLE_PER_HOST:
                idle.append((conn, time.monotonic()))
                return
        conn.close()

    def send(self, method: str, url: str, body: bytes | None, headers: dict[str, str], timeout: float) -> Reply:
        parts = urlsplit(url)
        scheme, host = parts.scheme, parts.hostname or ""
        key = (scheme, host, parts.port or (443 if scheme == "https" else 80))
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        retryable = method.upper() in _RETRYABLE
        conn = self._borrow(key) if retryable else None
        reused = conn is not None
        while True:
            if conn is None:
                conn = self._connect(scheme, host, key[2], timeout)
            started = time.monotonic()
            try:
                conn.request(method, target, body=body, headers=headers)
                resp = conn.getresponse()
                payload = resp.read()
            except _STALE:
                conn.close()
                if not reused:
                    raise
                conn, reused = None, False  # the server had closed it; try once on a new one
                continue
            except BaseException:
                conn.close()
                raise
            log.debug(
                "%s %s -> %s in %.0f ms%s",
                method,
                host,
                resp.status,
                (time.monotonic() - started) * 1000,
                " (reused connection)" if reused else "",
            )
            reply = Reply(resp.status, resp.reason, {k.lower(): v for k, v in resp.getheaders()}, payload)
            if resp.will_close:
                conn.close()
            else:
                self._give_back(key, conn)
            return reply

    def close(self) -> None:
        with self._lock:
            idle, self._idle = self._idle, {}
        for conns in idle.values():
            for conn, _ in conns:
                conn.close()


HTTP = HttpPool()


LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_icloud(host: str) -> bool:
    host = host.lower().rstrip(".")
    return host == "icloud.com" or host.endswith(".icloud.com")


def _safe_redirect(current: str, target: str) -> bool:
    """Only follow redirects that keep credentials on HTTPS and on the same service:
    iCloud may move us between its own servers (e.g. to p42-caldav.icloud.com); any
    other server only to itself."""
    src, dst = urlsplit(current), urlsplit(target)
    if dst.scheme not in ("https", src.scheme) or (src.scheme == "https" and dst.scheme != "https"):
        return False
    if not dst.hostname or not src.hostname:
        return False
    if _is_icloud(src.hostname):
        return _is_icloud(dst.hostname)
    return dst.hostname.lower() == src.hostname.lower() and dst.port == src.port


def require_https(url: str, setting: str) -> None:
    """Credentials are sent with every request, so plain HTTP is refused except to this machine."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        return
    if parts.scheme == "http" and (parts.hostname or "").lower() in LOCAL_HOSTS:
        return
    raise ToolError(f"{setting} must be an https:// URL (got {url!r}); plain HTTP would expose your password.")


def _allowed_destination(base_url: str, url: str) -> bool:
    """Where credentials may be sent: the same rules as redirects, applied to every request,
    including URLs the server hands back (principal, home set, collection and item links)."""
    base, dest = urlsplit(base_url), urlsplit(url)
    if dest.scheme == "http":
        return (
            base.scheme == "http"
            and (dest.hostname or "").lower() in LOCAL_HOSTS
            and (dest.hostname or "").lower() == (base.hostname or "").lower()
        )
    if dest.scheme != "https" or not dest.hostname or not base.hostname:
        return False
    if _is_icloud(base.hostname):
        return _is_icloud(dest.hostname)
    return dest.hostname.lower() == base.hostname.lower() and dest.port == base.port


def child_of(collection: str, url: str) -> bool:
    """True if url is a single resource directly inside collection (no dot segments or sub-paths)."""
    if not url.startswith(collection):
        return False
    rest = unquote(url[len(collection) :])
    return bool(rest) and "/" not in rest and "\\" not in rest and rest not in (".", "..")


# Where an account's calendars / address books live never changes while the server runs.
HOME_CACHE: TTLCache[str] = TTLCache(ttl=None)


class DavClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: int, service: str):
        self.base_url = base_url
        self.username = username
        self.service = service  # "iCloud Calendar" / "iCloud Contacts", for error messages
        self.timeout = timeout
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.auth = f"Basic {token}"

    def request(
        self,
        method: str,
        url: str,
        body: bytes | str | None = None,
        headers: dict | None = None,
        ok: tuple[int, ...] = (200, 201, 204, 207),
    ) -> tuple[int, bytes, str]:
        """Returns (status, body, final URL after redirects)."""
        url = urljoin(self.base_url, url)
        if not _allowed_destination(self.base_url, url):
            raise ToolError(f"{self.service}: refusing to send your credentials to {url}.")
        data = body.encode() if isinstance(body, str) else body
        request_headers = {"Authorization": self.auth, "User-Agent": "icloud-mail-mcp", **(headers or {})}
        for _ in range(5):
            try:
                reply = HTTP.send(method, url, data, request_headers, self.timeout)
            except (OSError, http.client.HTTPException) as e:
                raise ToolError(f"Could not reach {self.service} ({urlsplit(url).hostname}): {e}") from e
            if reply.status in (301, 302, 303, 307, 308) and reply.headers.get("location"):
                target = urljoin(url, reply.headers["location"])
                if not _safe_redirect(url, target):
                    raise ToolError(f"{self.service}: refusing to follow a redirect to {target}.")
                url = target
                continue
            if reply.status in ok:
                return reply.status, reply.body, url
            if reply.status == 401:
                raise ToolError(
                    f"{self.service} login failed. Check ICLOUD_APPLE_ID (your Apple ID email) "
                    "and the app-specific password."
                )
            if reply.status == 404:
                raise ToolError(f"{self.service}: not found ({url}).")
            if reply.status == 412:
                raise ToolError(f"{self.service}: it changed or already exists; list it again and retry.")
            raise DavError(
                f"{self.service} error {reply.status} {reply.reason} for {method} {url}.{_server_says(reply.body)}",
                reply.status,
            )
        raise ToolError(f"{self.service}: too many redirects for {url}.")

    def multistatus(self, method: str, url: str, body: str, depth: str) -> list[Response]:
        _, raw, final_url = self.request(
            method,
            url,
            body,
            {
                "Depth": depth,
                "Content-Type": 'application/xml; charset="utf-8"',
            },
        )
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            raise ToolError(f"{self.service} returned a response that could not be read.") from e
        results = []
        for resp in root.findall(tag("d:response")):
            href = resp.findtext(tag("d:href"), "").strip()
            item = Response(urljoin(final_url, href))
            for propstat in resp.findall(tag("d:propstat")):
                status = propstat.findtext(tag("d:status"), "")
                if " 200 " not in f"{status} ":
                    continue
                prop = propstat.find(tag("d:prop"))
                for child in prop if prop is not None else ():
                    item.props[child.tag] = child
            results.append(item)
        return results

    def propfind(self, url: str, props: list[str], depth: str = "0") -> list[Response]:
        inner = "".join(f"<{p}/>" for p in props)
        body = f"<d:propfind {xmlns()}><d:prop>{inner}</d:prop></d:propfind>"
        return self.multistatus("PROPFIND", url, body, depth)

    def report(self, url: str, body: str, depth: str = "1") -> list[Response]:
        return self.multistatus("REPORT", url, body, depth)

    def home_set(self, home_prop: str) -> str:
        """The user's calendar-home-set or addressbook-home-set URL (discovered once per process)."""
        return HOME_CACHE.get((self.base_url, self.username, home_prop), lambda: self._discover_home(home_prop))

    def _discover_home(self, home_prop: str) -> str:
        found = self.propfind(self.base_url, ["d:current-user-principal"])
        principal = found[0].href_in("d:current-user-principal") if found else None
        principal = urljoin(found[0].href, principal) if principal else None
        if not principal:
            raise ToolError(f"{self.service}: could not find your account (no current-user-principal).")
        found = self.propfind(principal, [home_prop])
        home = found[0].href_in(home_prop) if found else None
        if not home:
            raise ToolError(f"{self.service}: could not find your collections ({home_prop}).")
        return urljoin(found[0].href, home)


def xmlns() -> str:
    """Namespace declarations for the request bodies."""
    return " ".join(f'xmlns:{p}="{uri}"' for p, uri in NS.items())


def icloud_client(base_url: str, service: str) -> DavClient:
    """A CalDAV/CardDAV client signed in with the Apple ID and app-specific password."""
    settings = config.current()
    if not settings.apple_id:
        raise ToolError("ICLOUD_EMAIL is not set. Run `icloud-mail-mcp --setup`.")
    require_https(base_url, "ICLOUD_CALDAV_URL" if "Calendar" in service else "ICLOUD_CARDDAV_URL")
    return DavClient(base_url, settings.apple_id, config.password(settings), settings.timeout, service)
