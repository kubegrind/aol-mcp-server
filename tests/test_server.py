import email
import email.mime.multipart
import email.mime.text
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("AOL_EMAIL", "test@aol.com")
os.environ.setdefault("AOL_APP_PASSWORD", "test_app_password")

import server  # noqa: E402


def _build_raw_email(
    from_: str = "sender@example.com",
    subject: str = "Test Subject",
    body: str = "Hello, world!",
    date: str = "Mon, 01 Jan 2024 12:00:00 +0000",
    msg_id: str = "<test@example.com>",
) -> bytes:
    """Return a minimal RFC 2822 email as raw bytes."""
    msg = email.mime.multipart.MIMEMultipart()
    msg["From"] = from_
    msg["Subject"] = subject
    msg["Date"] = date
    msg["Message-ID"] = msg_id
    msg.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))
    return msg.as_bytes()


RAW_EMAIL = _build_raw_email()

FETCH_OK = [
    (b"1 (RFC822 {512})", RAW_EMAIL),
    b")",
]
FETCH_MISSING = [None]


def _mock_imap(
    message_ids: bytes = b"1 2 3",
    fetch_data=None,
) -> MagicMock:
    imap = MagicMock()
    imap.select.return_value = ("OK", [b"3"])
    imap.search.return_value = ("OK", [message_ids])
    imap.fetch.return_value = ("OK", fetch_data or FETCH_OK)
    imap.copy.return_value = ("OK", [b""])
    imap.store.return_value = ("OK", [b""])
    imap.expunge.return_value = ("OK", [b""])
    imap.list.return_value = (
        "OK",
        [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "Trash"',
            b'(\\HasNoChildren) "/" "Sent"',
        ],
    )
    imap.logout.return_value = ("BYE", [b""])
    return imap


def _mock_smtp() -> MagicMock:
    smtp = MagicMock()
    smtp.sendmail.return_value = {}
    smtp.quit.return_value = ("221", b"")
    return smtp


class TestDecodeHeader:
    def test_plain_ascii(self):
        assert server._decode_header("Hello World") == "Hello World"

    def test_empty_string(self):
        assert server._decode_header("") == ""

    def test_rfc2047_base64(self):
        assert server._decode_header("=?utf-8?b?SGVsbG8gV29ybGQ=?=") == "Hello World"

    def test_none_like_empty(self):
        assert server._decode_header("") == ""


class TestExtractBody:
    def test_plain_text_message(self):
        msg = email.message_from_bytes(_build_raw_email(body="Plain body text"))
        assert "Plain body text" in server._extract_body(msg)

    def test_prefers_plain_over_html(self):
        outer = email.mime.multipart.MIMEMultipart("alternative")
        outer.attach(email.mime.text.MIMEText("plain version", "plain"))
        outer.attach(email.mime.text.MIMEText("<b>html version</b>", "html"))
        parsed = email.message_from_bytes(outer.as_bytes())
        assert "plain version" in server._extract_body(parsed)

    def test_html_fallback_when_no_plain(self):
        outer = email.mime.multipart.MIMEMultipart()
        outer.attach(email.mime.text.MIMEText("<p>only html</p>", "html"))
        parsed = email.message_from_bytes(outer.as_bytes())
        assert "only html" in server._extract_body(parsed)

    def test_empty_multipart_returns_placeholder(self):
        outer = email.mime.multipart.MIMEMultipart()
        parsed = email.message_from_bytes(outer.as_bytes())
        assert server._extract_body(parsed) == "(no body)"


class TestPreview:
    def test_short_text_unchanged(self):
        assert server._preview("Hello") == "Hello"

    def test_long_text_truncated_with_ellipsis(self):
        result = server._preview("A" * 200)
        assert result.endswith("…")
        assert len(result) <= 125

    def test_newlines_removed(self):
        assert "\n" not in server._preview("line one\nline two")

    def test_custom_length(self):
        result = server._preview("A" * 50, length=10)
        assert len(result) <= 15


class TestReadInbox:
    def test_returns_formatted_emails(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()):
            result = server.read_inbox(count=3)
        assert "ID:" in result
        assert "From:" in result
        assert "Subject:" in result

    def test_count_clamped_to_50(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()):
            result = server.read_inbox(count=999)
        assert "Error" not in result

    def test_empty_inbox(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap(message_ids=b"")):
            result = server.read_inbox()
        assert "empty" in result.lower()

    def test_connection_error_returns_string(self):
        with patch("imaplib.IMAP4_SSL", side_effect=ConnectionRefusedError("refused")):
            result = server.read_inbox()
        assert result.startswith("Error")

    def test_auth_error_returns_string(self):
        with patch("imaplib.IMAP4_SSL", side_effect=Exception("AUTHENTICATE failed")):
            result = server.read_inbox()
        assert "Error" in result


class TestReadEmail:
    def test_returns_full_email_content(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()):
            result = server.read_email("1")
        assert "From:" in result
        assert "Subject:" in result
        assert "Date:" in result

    def test_message_not_found(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap(fetch_data=FETCH_MISSING)):
            result = server.read_email("999")
        assert "not found" in result.lower()

    def test_error_returns_string(self):
        with patch("imaplib.IMAP4_SSL", side_effect=Exception("timeout")):
            result = server.read_email("1")
        assert result.startswith("Error")


class TestSearchEmails:
    def test_returns_matches(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()):
            result = server.search_emails("hello")
        assert "Found" in result or "match" in result.lower()

    def test_no_results_message(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap(message_ids=b"")):
            result = server.search_emails("xyzzy_not_found")
        assert "No emails found" in result

    def test_invalid_search_in_rejected(self):
        result = server.search_emails("test", search_in="INVALID")
        assert "must be one of" in result

    @pytest.mark.parametrize("field", ["ALL", "FROM", "SUBJECT", "BODY"])
    def test_valid_search_fields(self, field):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()):
            result = server.search_emails("test", search_in=field)
        assert "must be one of" not in result

    def test_count_clamped(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()):
            result = server.search_emails("test", count=999)
        assert "Error" not in result


class TestSendEmail:
    def test_send_success(self):
        with patch("smtplib.SMTP_SSL", return_value=_mock_smtp()):
            result = server.send_email("to@example.com", "Subject", "Body text")
        assert "sent" in result.lower()

    def test_multiple_recipients(self):
        smtp = _mock_smtp()
        with patch("smtplib.SMTP_SSL", return_value=smtp):
            result = server.send_email("a@x.com, b@x.com, c@x.com", "Hi", "Body")
        assert "Error" not in result
        call_args = smtp.sendmail.call_args[0]
        assert len(call_args[1]) == 3

    def test_smtp_auth_failure_returns_string(self):
        with patch("smtplib.SMTP_SSL", side_effect=Exception("auth failed")):
            result = server.send_email("to@example.com", "Subject", "Body")
        assert result.startswith("Error")


class TestReplyEmail:
    def test_reply_sent(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()), \
             patch("smtplib.SMTP_SSL", return_value=_mock_smtp()):
            result = server.reply_email("1", "Got it, thanks!")
        assert "sent" in result.lower()

    def test_reply_sets_re_prefix(self):
        smtp = _mock_smtp()
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()), \
             patch("smtplib.SMTP_SSL", return_value=smtp):
            server.reply_email("1", "Reply body")
        raw_msg = smtp.sendmail.call_args[0][2]
        assert "Re:" in raw_msg

    def test_already_re_not_doubled(self):
        raw = _build_raw_email(subject="Re: Existing Thread")
        mock_imap = _mock_imap(fetch_data=[(b"1", raw), b")"])
        smtp = _mock_smtp()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap), \
             patch("smtplib.SMTP_SSL", return_value=smtp):
            server.reply_email("1", "body")
        raw_msg = smtp.sendmail.call_args[0][2]
        assert "Re: Re:" not in raw_msg

    def test_email_not_found(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap(fetch_data=FETCH_MISSING)):
            result = server.reply_email("999", "body")
        assert "not found" in result.lower()


class TestDeleteEmail:
    def test_moves_to_trash(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()):
            result = server.delete_email("1")
        assert "moved" in result.lower() or "Trash" in result

    def test_copy_failure_returns_string(self):
        mock_imap = _mock_imap()
        mock_imap.copy.return_value = ("NO", [b"permission denied"])
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.delete_email("1")
        assert "Could not" in result or "Error" in result

    def test_expunge_called_after_delete_flag(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            server.delete_email("1")
        mock_imap.expunge.assert_called_once()


class TestMoveEmail:
    def test_move_success(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()):
            result = server.move_email("1", "Work")
        assert "moved" in result.lower()
        assert "Work" in result

    def test_unknown_folder_returns_hint(self):
        mock_imap = _mock_imap()
        mock_imap.copy.return_value = ("NO", [b"no such mailbox"])
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.move_email("1", "DoesNotExist")
        assert "list_folders" in result or "Could not" in result

    def test_error_returns_string(self):
        with patch("imaplib.IMAP4_SSL", side_effect=Exception("conn reset")):
            result = server.move_email("1", "Archive")
        assert result.startswith("Error")


class TestListFolders:
    def test_returns_folder_names(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()):
            result = server.list_folders()
        assert "INBOX" in result
        assert "Trash" in result

    def test_empty_folder_list(self):
        mock_imap = _mock_imap()
        mock_imap.list.return_value = ("OK", [])
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.list_folders()
        assert "No folders" in result

    def test_error_returns_string(self):
        with patch("imaplib.IMAP4_SSL", side_effect=Exception("network error")):
            result = server.list_folders()
        assert result.startswith("Error")


class TestMarkRead:
    def test_single_id_marked(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()):
            result = server.mark_read("42")
        assert "42" in result
        assert "Marked" in result

    def test_multiple_ids_all_marked(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()):
            result = server.mark_read("1,2,3")
        assert "1" in result and "2" in result and "3" in result

    def test_empty_input_rejected(self):
        result = server.mark_read("  ,  ,  ")
        assert "No message IDs" in result

    def test_partial_failure_reported(self):
        mock_imap = _mock_imap()
        mock_imap.store.side_effect = [
            ("OK", [b""]),
            ("NO", [b"failed"]),
            ("OK", [b""]),
        ]
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.mark_read("10,11,12")
        assert "Failed" in result
        assert "Marked" in result

    def test_store_uses_seen_flag(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            server.mark_read("5")
        args = mock_imap.store.call_args[0]
        assert "\\Seen" in args


class TestGetAttachments:
    def test_no_attachments_in_plain_email(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()):
            result = server.get_attachments("1")
        assert "No attachments" in result

    def test_attachment_listed(self):
        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = "s@example.com"
        msg["Subject"] = "Has Attachment"
        msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
        msg["Message-ID"] = "<att@example.com>"
        msg.attach(email.mime.text.MIMEText("body text", "plain"))

        att = email.mime.text.MIMEText("file data here", "plain")
        att.add_header("Content-Disposition", "attachment", filename="report.txt")
        msg.attach(att)

        mock_imap = _mock_imap(fetch_data=[(b"1 (RFC822 {1024})", msg.as_bytes()), b")"])
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.get_attachments("1")

        assert "report.txt" in result
        assert "text/plain" in result

    def test_size_formatted_as_bytes(self):
        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = "s@example.com"
        msg["Subject"] = "S"
        msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
        msg["Message-ID"] = "<s@example.com>"
        att = email.mime.text.MIMEText("x" * 500, "plain")
        att.add_header("Content-Disposition", "attachment", filename="small.txt")
        msg.attach(att)

        mock_imap = _mock_imap(fetch_data=[(b"1", msg.as_bytes()), b")"])
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.get_attachments("1")
        assert " B" in result or "KB" in result

    def test_email_not_found(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap(fetch_data=FETCH_MISSING)):
            result = server.get_attachments("999")
        assert "not found" in result.lower()

    def test_error_returns_string(self):
        with patch("imaplib.IMAP4_SSL", side_effect=Exception("imap down")):
            result = server.get_attachments("1")
        assert result.startswith("Error")
