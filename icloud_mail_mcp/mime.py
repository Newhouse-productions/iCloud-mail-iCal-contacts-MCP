"""MIME handling with no network access: message structure, decoding, filenames.

`structure_parts` reads an IMAP BODYSTRUCTURE; `email_parts` gives the same view of a
fully downloaded message. Both produce `Part`s addressed by IMAP section number.
"""

from __future__ import annotations

import base64
import quopri
import re
from dataclasses import dataclass
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage, Message, MIMEPart
from email.parser import BytesParser, Parser
from html import unescape
from html.parser import HTMLParser
from pathlib import Path


class _HTMLText(HTMLParser):
    """Minimal HTML to text: drops script/style/head, keeps line breaks."""

    SKIP = {"script", "style", "head", "title"}
    BREAKS = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.BREAKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(html)
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        text = unescape(re.sub(r"<[^>]+>", " ", html))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def parse(raw: bytes) -> EmailMessage:
    return BytesParser(policy=policy.default).parsebytes(raw)


_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_name(name: str) -> str:
    """A filename safe on Mac and Windows: no path parts, reserved characters or names."""
    cleaned = re.sub(r"[^\w.\- ]", "_", name).strip(" .")
    stem, suffix = Path(cleaned).stem, Path(cleaned).suffix[:16]
    if stem.lower() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    return f"{stem[:120]}{suffix}" if stem else ""


def decode_words(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


@dataclass
class Part:
    """One leaf of a message's MIME tree, addressed by its IMAP section number."""

    section: str
    content_type: str
    encoding: str
    size: int  # encoded size on the server, in bytes
    filename: str | None
    disposition: str | None
    charset: str | None

    @property
    def is_attachment(self) -> bool:
        return self.content_type == "message/rfc822" or bool(self.filename) or self.disposition == "attachment"

    @property
    def decoded_size(self) -> int:
        # base64 stores 57 bytes per 76-character line plus CRLF.
        return self.size * 57 // 78 if self.encoding == "base64" else self.size


def as_str(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return "" if value is None else str(value)


def _param_text(params) -> str:
    """Render a BODYSTRUCTURE parameter list back into header syntax."""
    items = list(params or ())
    out = []
    # A malformed odd-length list from a server loses its dangling key rather than failing.
    for key, val in zip(items[::2], items[1::2], strict=False):
        k = as_str(key)
        v = re.sub(r"[\r\n]+", " ", as_str(val))
        if k.endswith("*"):  # RFC 2231 encoded value: already in header syntax
            out.append(f"; {k}={v}")
        else:
            out.append('; {}="{}"'.format(k, v.replace("\\", "\\\\").replace('"', '\\"')))
    return "".join(out)


def mime_details(content_type: str, params, disposition) -> tuple[str | None, str | None, str | None]:
    """Filename, disposition and charset, decoded by the email package (RFC 2231 and 2047)."""
    lines = [f"Content-Type: {content_type}{_param_text(params)}"]
    disp_type = None
    if isinstance(disposition, (tuple, list)) and disposition and disposition[0]:
        disp_type = as_str(disposition[0]).lower()
        disp_params = disposition[1] if len(disposition) > 1 else None
        lines.append(f"Content-Disposition: {disp_type}{_param_text(disp_params)}")
    headers = Parser(policy=policy.default).parsestr("\n".join(lines) + "\n\n", headersonly=True)
    return headers.get_filename(), disp_type, headers.get_content_charset()


def _is_multipart(structure) -> bool:
    return isinstance(structure, (tuple, list)) and bool(structure) and isinstance(structure[0], list)


def structure_parts(structure, section: str = "") -> list[Part]:
    """Flatten an IMAP BODYSTRUCTURE into its leaf parts (attached emails are leaves)."""
    if _is_multipart(structure):
        parts = []
        for i, child in enumerate(structure[0], 1):
            parts += structure_parts(child, f"{section}.{i}" if section else str(i))
        return parts
    content_type = re.sub(r"[^\w.+\-/]", "", f"{as_str(structure[0])}/{as_str(structure[1])}".lower())
    # Where the disposition sits depends on the part type (RFC 3501 section 7.4.2).
    if content_type == "message/rfc822":
        disp_at = 11
    elif content_type.startswith("text/"):
        disp_at = 9
    else:
        disp_at = 8
    disposition = structure[disp_at] if len(structure) > disp_at else None
    filename, disp_type, charset = mime_details(content_type, structure[2], disposition)
    if content_type == "message/rfc822" and not filename:
        envelope = structure[7] if len(structure) > 7 else None
        subject = decode_words(as_str(envelope[1])) if envelope and len(envelope) > 1 and envelope[1] else ""
        filename = f"{safe_name(subject)[:80] or 'attached message'}.eml"
    return [
        Part(
            section=section or "1",
            content_type=content_type,
            encoding=as_str(structure[5]).lower() or "7bit",
            size=int(structure[6] or 0),
            filename=filename,
            disposition=disp_type,
            charset=charset,
        )
    ]


def email_parts(msg: EmailMessage) -> tuple[list[Part], dict[str, bytes]]:
    """The same leaf parts as structure_parts, from a fully downloaded message (fallback)."""
    parts: list[Part] = []
    raw: dict[str, bytes] = {}

    def visit(part: MIMEPart, section: str) -> None:
        if part.get_content_maintype() == "multipart":
            for i, sub in enumerate(part.iter_parts(), 1):
                visit(sub, f"{section}.{i}" if section else str(i))
            return
        sec = section or "1"
        content_type = part.get_content_type()
        name: str | None
        if content_type == "message/rfc822":
            inner = part.get_payload(0) if part.is_multipart() else None
            if isinstance(inner, Message):
                data, subject = inner.as_bytes(), str(inner.get("Subject", ""))
            else:
                data, subject = b"", ""
            name = part.get_filename() or f"{safe_name(subject)[:80] or 'attached message'}.eml"
            encoding = "7bit"
        else:
            payload = part.get_payload()
            data = payload.encode("utf-8", "surrogateescape") if isinstance(payload, str) else b""
            name = part.get_filename()
            encoding = str(part.get("Content-Transfer-Encoding", "7bit")).strip().lower()
        raw[sec] = data
        parts.append(
            Part(
                sec, content_type, encoding, len(data), name, part.get_content_disposition(), part.get_content_charset()
            )
        )

    visit(msg, "")
    return parts, raw


def decode_transfer(data: bytes, encoding: str) -> bytes:
    """Undo base64 / quoted-printable. Tolerates data cut off by a partial fetch."""
    try:
        if encoding == "base64":
            data = re.sub(rb"[^A-Za-z0-9+/=]", b"", data)
            return base64.b64decode(data[: len(data) - len(data) % 4])
        if encoding == "quoted-printable":
            return quopri.decodestring(data)
    except ValueError:
        return b""
    return data


def part_text(part: Part, data: bytes) -> str:
    raw = decode_transfer(data, part.encoding)
    try:
        text = raw.decode(part.charset or "utf-8", "replace")
    except LookupError:
        text = raw.decode("utf-8", "replace")
    return html_to_text(text) if part.content_type == "text/html" else text.strip()


def pick_body(parts: list[Part]) -> Part | None:
    for wanted in ("text/plain", "text/html"):
        for part in parts:
            if part.content_type == wanted and not part.is_attachment:
                return part
    return None
