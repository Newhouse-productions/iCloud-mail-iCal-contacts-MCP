"""MIME handling with no network: HTML to text, filenames, structure parsing."""

from icloud_mail_mcp import mime


def test_html_conversion_drops_script_and_style():
    text = mime.html_to_text("<style>.x{color:red}</style><p>Hi&nbsp;there</p><script>evil()</script>")
    assert "Hi" in text and "there" in text
    assert "color" not in text and "evil" not in text


def test_safe_name_handles_windows_and_long_names():
    assert mime.safe_name("CON.pdf") == "_CON.pdf"
    assert mime.safe_name("aux") == "_aux"
    long = mime.safe_name("x" * 300 + ".pdf")
    assert long.endswith(".pdf") and len(long) <= 140
    assert "/" not in mime.safe_name("../../etc/passwd")


def test_rfc2231_and_rfc2047_filenames_decode():
    disposition = (b"attachment", (b"filename*", b"utf-8''r%C3%A9sum%C3%A9%20tr%C3%A8s%20long.pdf"))
    assert mime.mime_details("application/pdf", None, disposition)[0] == "résumé très long.pdf"
    assert mime.mime_details("application/pdf", (b"name", b"=?utf-8?q?caf=C3=A9.pdf?="), None)[0] == "café.pdf"


def test_structure_parts_numbers_nested_sections():
    structure = (
        [
            (b"text", b"plain", (b"charset", b"us-ascii"), None, None, b"7bit", 12, 0, None, None, None, None),
            (
                [
                    (b"text", b"html", (b"charset", b"us-ascii"), None, None, b"7bit", 19, 0, None, None, None, None),
                    (
                        b"application",
                        b"pdf",
                        None,
                        None,
                        None,
                        b"base64",
                        18,
                        None,
                        (b"inline", (b"filename", b"doc.pdf")),
                        None,
                        None,
                    ),
                ],
                b"mixed",
                None,
                None,
                None,
                None,
            ),
        ],
        b"alternative",
        None,
        None,
        None,
        None,
    )
    parts = mime.structure_parts(structure)
    assert [(p.section, p.content_type, p.is_attachment) for p in parts] == [
        ("1", "text/plain", False),
        ("2.1", "text/html", False),
        ("2.2", "application/pdf", True),
    ]
    assert mime.pick_body(parts).section == "1"


def test_injected_header_text_in_mime_type_is_neutralised():
    [part] = mime.structure_parts(
        (b"text\r\nX-Evil: 1", b"plain", None, None, None, b"7bit", 3, 1, None, None, None, None)
    )
    assert "\n" not in part.content_type and ":" not in part.content_type


def test_partial_base64_and_quoted_printable_decode():
    assert mime.decode_transfer(b"aGVsbG8gd29y\r\nbGQ=", "base64") == b"hello world"
    assert mime.decode_transfer(b"aGVsbG8gd29yb", "base64") == b"hello wor"  # cut mid-quantum
    assert mime.decode_transfer(b"caf=C3=A9", "quoted-printable") == "café".encode()
