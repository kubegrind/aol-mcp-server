import email
import email.mime.multipart
import email.mime.text
import os
from unittest.mock import MagicMock, patch
from unittest.mock import mock_open as _mock_open

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

    def _uid(cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [message_ids])
        return ("OK", [b""])

    imap.uid.side_effect = _uid
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

    def test_count_clamped_to_100(self):
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

    def test_send_has_date_header(self):
        smtp = _mock_smtp()
        with patch("smtplib.SMTP_SSL", return_value=smtp):
            server.send_email("to@example.com", "Subject", "Body")
        raw_msg = smtp.sendmail.call_args[0][2]
        assert "Date:" in raw_msg

    def test_send_is_plain_text_not_multipart(self):
        smtp = _mock_smtp()
        with patch("smtplib.SMTP_SSL", return_value=smtp):
            server.send_email("to@example.com", "Subject", "Body")
        raw_msg = smtp.sendmail.call_args[0][2]
        assert "Content-Type: text/plain" in raw_msg
        assert "multipart" not in raw_msg

    def test_send_with_cc(self):
        smtp = _mock_smtp()
        with patch("smtplib.SMTP_SSL", return_value=smtp):
            result = server.send_email("to@x.com", "Hi", "Body", cc="cc@x.com")
        assert "Error" not in result
        recipients = smtp.sendmail.call_args[0][1]
        assert "cc@x.com" in recipients
        raw_msg = smtp.sendmail.call_args[0][2]
        assert "cc@x.com" in raw_msg

    def test_send_with_bcc_in_recipients_not_headers(self):
        smtp = _mock_smtp()
        with patch("smtplib.SMTP_SSL", return_value=smtp):
            result = server.send_email("to@x.com", "Hi", "Body", bcc="bcc@x.com")
        assert "Error" not in result
        recipients = smtp.sendmail.call_args[0][1]
        assert "bcc@x.com" in recipients
        raw_msg = smtp.sendmail.call_args[0][2]
        assert "bcc@x.com" not in raw_msg

    def test_send_with_attachment(self):
        smtp = _mock_smtp()
        with (
            patch("smtplib.SMTP_SSL", return_value=smtp),
            patch("os.path.isfile", return_value=True),
            patch("builtins.open", _mock_open(read_data=b"file content")),
        ):
            result = server.send_email("to@x.com", "Hi", "Body", attachments="/path/report.pdf")
        assert "Error" not in result
        raw_msg = smtp.sendmail.call_args[0][2]
        assert "report.pdf" in raw_msg
        assert "multipart" in raw_msg

    def test_missing_attachment_returns_error(self):
        with (
            patch("smtplib.SMTP_SSL", return_value=_mock_smtp()),
            patch("os.path.isfile", return_value=False),
        ):
            result = server.send_email("to@x.com", "Hi", "Body", attachments="/missing.pdf")
        assert "not found" in result.lower()

    def test_smtp_auth_failure_returns_string(self):
        with patch("smtplib.SMTP_SSL", side_effect=Exception("auth failed")):
            result = server.send_email("to@example.com", "Subject", "Body")
        assert result.startswith("Error")


class TestReplyEmail:
    def test_reply_sent(self):
        with (
            patch("imaplib.IMAP4_SSL", return_value=_mock_imap()),
            patch("smtplib.SMTP_SSL", return_value=_mock_smtp()),
        ):
            result = server.reply_email("1", "Got it, thanks!")
        assert "sent" in result.lower()

    def test_reply_sets_re_prefix(self):
        smtp = _mock_smtp()
        with (
            patch("imaplib.IMAP4_SSL", return_value=_mock_imap()),
            patch("smtplib.SMTP_SSL", return_value=smtp),
        ):
            server.reply_email("1", "Reply body")
        raw_msg = smtp.sendmail.call_args[0][2]
        assert "Re:" in raw_msg

    def test_already_re_not_doubled(self):
        raw = _build_raw_email(subject="Re: Existing Thread")
        mock_imap = _mock_imap(fetch_data=[(b"1", raw), b")"])
        smtp = _mock_smtp()
        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_imap),
            patch("smtplib.SMTP_SSL", return_value=smtp),
        ):
            server.reply_email("1", "body")
        raw_msg = smtp.sendmail.call_args[0][2]
        assert "Re: Re:" not in raw_msg

    def test_reply_has_date_header(self):
        smtp = _mock_smtp()
        with (
            patch("imaplib.IMAP4_SSL", return_value=_mock_imap()),
            patch("smtplib.SMTP_SSL", return_value=smtp),
        ):
            server.reply_email("1", "body")
        raw_msg = smtp.sendmail.call_args[0][2]
        assert "Date:" in raw_msg

    def test_reply_with_cc(self):
        smtp = _mock_smtp()
        with (
            patch("imaplib.IMAP4_SSL", return_value=_mock_imap()),
            patch("smtplib.SMTP_SSL", return_value=smtp),
        ):
            server.reply_email("1", "body", cc="cc@x.com")
        recipients = smtp.sendmail.call_args[0][1]
        assert "cc@x.com" in recipients
        raw_msg = smtp.sendmail.call_args[0][2]
        assert "cc@x.com" in raw_msg

    def test_reply_with_bcc_not_in_headers(self):
        smtp = _mock_smtp()
        with (
            patch("imaplib.IMAP4_SSL", return_value=_mock_imap()),
            patch("smtplib.SMTP_SSL", return_value=smtp),
        ):
            server.reply_email("1", "body", bcc="bcc@x.com")
        recipients = smtp.sendmail.call_args[0][1]
        assert "bcc@x.com" in recipients
        raw_msg = smtp.sendmail.call_args[0][2]
        assert "bcc@x.com" not in raw_msg

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


class TestFolderParameters:
    def test_read_inbox_custom_folder(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.read_inbox(count=3, folder="LinkedIn")
        mock_imap.select.assert_called_with("LinkedIn")
        assert "Error" not in result

    def test_read_inbox_empty_custom_folder(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap(message_ids=b"")):
            result = server.read_inbox(folder="Spam")
        assert "empty" in result.lower()

    def test_read_email_custom_folder(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.read_email("1", folder="Sent")
        mock_imap.select.assert_called_with("Sent")
        assert "From:" in result

    def test_search_emails_custom_folder(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.search_emails("job", folder="Indeed")
        mock_imap.select.assert_called_with("Indeed")
        assert "Indeed" in result

    def test_search_emails_no_results_includes_folder(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap(message_ids=b"")):
            result = server.search_emails("xyzzy", folder="GitHub")
        assert "GitHub" in result

    def test_reply_email_custom_folder(self):
        mock_imap = _mock_imap()
        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_imap),
            patch("smtplib.SMTP_SSL", return_value=_mock_smtp()),
        ):
            server.reply_email("1", "Thanks!", folder="Sent")
        mock_imap.select.assert_called_with("Sent")

    def test_delete_email_custom_folder(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.delete_email("1", folder="LinkedIn")
        mock_imap.select.assert_called_with("LinkedIn")
        assert "moved" in result.lower() or "Trash" in result

    def test_move_email_with_source_folder(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.move_email("1", "Archive", source_folder="GitHub")
        mock_imap.select.assert_called_with("GitHub")
        assert "moved" in result.lower()

    def test_mark_read_custom_folder(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.mark_read("5", folder="LinkedIn")
        mock_imap.select.assert_called_with("LinkedIn")
        assert "Marked" in result

    def test_get_attachments_custom_folder(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.get_attachments("1", folder="GitHub")
        mock_imap.select.assert_called_with("GitHub")
        assert "No attachments" in result or "Attachments" in result


class TestReadFolder:
    def test_returns_emails_from_folder(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.read_folder("LinkedIn")
        mock_imap.select.assert_called_with("LinkedIn")
        assert "ID:" in result

    def test_empty_folder(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap(message_ids=b"")):
            result = server.read_folder("EmptyFolder")
        assert "empty" in result.lower()

    def test_count_clamped_to_50(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()):
            result = server.read_folder("Spam", count=999)
        assert "Error" not in result

    def test_folder_name_in_output(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap()):
            result = server.read_folder("GitHub", count=1)
        assert "GitHub" in result

    def test_connection_error_returns_string(self):
        with patch("imaplib.IMAP4_SSL", side_effect=Exception("timeout")):
            result = server.read_folder("LinkedIn")
        assert result.startswith("Error")


class TestDeleteAllInFolder:
    def test_deletes_all_emails(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.delete_all_in_folder("Spam")
        mock_imap.select.assert_called_with("Spam")
        assert "Deleted" in result
        assert "3" in result
        mock_imap.expunge.assert_called_once()

    def test_uses_uid_commands(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            server.delete_all_in_folder("LinkedIn")
        uid_cmds = [call[0][0] for call in mock_imap.uid.call_args_list]
        assert "SEARCH" in uid_cmds
        assert "COPY" in uid_cmds
        assert "STORE" in uid_cmds

    def test_empty_folder_message(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap(message_ids=b"")):
            result = server.delete_all_in_folder("Spam")
        assert "already empty" in result.lower()

    def test_copy_failure_returns_error(self):
        mock_imap = _mock_imap()

        def _uid_fail(cmd, *args):
            if cmd == "SEARCH":
                return ("OK", [b"1 2 3"])
            if cmd == "COPY":
                return ("NO", [b"permission denied"])
            return ("OK", [b""])

        mock_imap.uid.side_effect = _uid_fail
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.delete_all_in_folder("LinkedIn")
        assert "Could not" in result
        mock_imap.expunge.assert_not_called()

    def test_error_returns_string(self):
        with patch("imaplib.IMAP4_SSL", side_effect=Exception("imap down")):
            result = server.delete_all_in_folder("Spam")
        assert result.startswith("Error")


class TestMoveAllEmails:
    def test_moves_all_emails(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.move_all_emails("LinkedIn", "Archive")
        mock_imap.select.assert_called_with("LinkedIn")
        assert "Moved" in result
        assert "LinkedIn" in result
        assert "Archive" in result
        mock_imap.expunge.assert_called_once()

    def test_uses_uid_commands(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            server.move_all_emails("GitHub", "Archive")
        uid_cmds = [call[0][0] for call in mock_imap.uid.call_args_list]
        assert "SEARCH" in uid_cmds
        assert "COPY" in uid_cmds
        assert "STORE" in uid_cmds

    def test_empty_source_folder(self):
        with patch("imaplib.IMAP4_SSL", return_value=_mock_imap(message_ids=b"")):
            result = server.move_all_emails("EmptyFolder", "Archive")
        assert "empty" in result.lower()

    def test_copy_failure_returns_error_with_hint(self):
        mock_imap = _mock_imap()

        def _uid_fail(cmd, *args):
            if cmd == "SEARCH":
                return ("OK", [b"1 2 3"])
            if cmd == "COPY":
                return ("NO", [b"no such mailbox"])
            return ("OK", [b""])

        mock_imap.uid.side_effect = _uid_fail
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.move_all_emails("GitHub", "NonExistentFolder")
        assert "list_folders" in result or "Could not" in result
        mock_imap.expunge.assert_not_called()

    def test_uid_set_includes_all_messages(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            server.move_all_emails("Spam", "INBOX")
        copy_call = next(c for c in mock_imap.uid.call_args_list if c[0][0] == "COPY")
        uid_set = copy_call[0][1]
        assert b"1" in uid_set and b"2" in uid_set and b"3" in uid_set

    def test_error_returns_string(self):
        with patch("imaplib.IMAP4_SSL", side_effect=Exception("connection reset")):
            result = server.move_all_emails("LinkedIn", "Archive")
        assert result.startswith("Error")


class TestBugFixes:
    def test_reply_uses_bare_address_for_smtp(self):
        raw = _build_raw_email(from_="John Doe <john@example.com>")
        mock_imap = _mock_imap(fetch_data=[(b"1", raw), b")"])
        smtp = _mock_smtp()
        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_imap),
            patch("smtplib.SMTP_SSL", return_value=smtp),
        ):
            server.reply_email("1", "body")
        smtp_recipients = smtp.sendmail.call_args[0][1]
        assert smtp_recipients == ["john@example.com"]

    def test_reply_to_header_keeps_display_name(self):
        raw = _build_raw_email(from_="John Doe <john@example.com>")
        mock_imap = _mock_imap(fetch_data=[(b"1", raw), b")"])
        smtp = _mock_smtp()
        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_imap),
            patch("smtplib.SMTP_SSL", return_value=smtp),
        ):
            server.reply_email("1", "body")
        raw_msg = smtp.sendmail.call_args[0][2]
        assert "John Doe" in raw_msg

    def test_imap_folder_quotes_spaces(self):
        assert server._imap_folder("INBOX") == "INBOX"
        assert server._imap_folder("Deleted Items") == '"Deleted Items"'
        assert server._imap_folder("Bulk Mail") == '"Bulk Mail"'
        assert server._imap_folder("Trash") == "Trash"

    def test_delete_email_quotes_spaced_trash_folder(self):
        mock_imap = _mock_imap()
        mock_imap.list.return_value = (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren) "/" "Deleted Items"',
            ],
        )
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            server.delete_email("1")
        copy_args = mock_imap.copy.call_args[0]
        assert copy_args[1] == '"Deleted Items"'

    def test_move_email_quotes_spaced_destination(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            server.move_email("1", "Bulk Mail")
        copy_args = mock_imap.copy.call_args[0]
        assert copy_args[1] == '"Bulk Mail"'

    def test_search_strips_quotes_from_query(self):
        mock_imap = _mock_imap()
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.search_emails('say "hello" world')
        assert "Error" not in result
        search_call = mock_imap.search.call_args[0][1]
        assert '"' not in search_call.replace("(", "").replace(")", "").split('"')[0]

    def test_read_inbox_skips_non_tuple_fetch_response(self):
        mock_imap = _mock_imap()
        mock_imap.fetch.return_value = ("OK", [b")"])
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.read_inbox()
        assert "Error" not in result

    def test_read_folder_skips_non_tuple_fetch_response(self):
        mock_imap = _mock_imap()
        mock_imap.fetch.return_value = ("OK", [b")"])
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.read_folder("LinkedIn")
        assert "Error" not in result

    def test_search_emails_skips_non_tuple_fetch_response(self):
        mock_imap = _mock_imap()
        mock_imap.fetch.return_value = ("OK", [b")"])
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = server.search_emails("hello")
        assert "Error" not in result
