"""AOL Mail MCP Server — production-ready stdio server using FastMCP."""

import email
import email.header
import email.mime.multipart
import email.mime.text
import html
import html.parser
import imaplib
import os
import re
import smtplib
from contextlib import contextmanager

from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

AOL_EMAIL: str = os.getenv("AOL_EMAIL", "")
AOL_APP_PASSWORD: str = os.getenv("AOL_APP_PASSWORD", "")

IMAP_HOST = "imap.aol.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.aol.com"
SMTP_PORT = 465

mcp = FastMCP("AOL Mail")


@contextmanager
def _imap():
    """Open an SSL IMAP session and guarantee logout on exit."""
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        conn.login(AOL_EMAIL, AOL_APP_PASSWORD)
        yield conn
    finally:
        if conn:
            try:
                conn.logout()
            except Exception:
                pass


@contextmanager
def _smtp():
    """Open an SSL SMTP session and guarantee quit on exit."""
    conn = None
    try:
        conn = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
        conn.login(AOL_EMAIL, AOL_APP_PASSWORD)
        yield conn
    finally:
        if conn:
            try:
                conn.quit()
            except Exception:
                pass


def _strip_html(raw_html: str) -> str:
    """Convert HTML to readable plain text using stdlib only."""

    class _Stripper(html.parser.HTMLParser):
        _SKIP = {"script", "style", "head"}

        def __init__(self) -> None:
            super().__init__()
            self._parts: list[str] = []
            self._skip = 0

        def handle_starttag(self, tag: str, attrs: object) -> None:
            if tag in self._SKIP:
                self._skip += 1
            if tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
                self._parts.append("\n")

        def handle_endtag(self, tag: str) -> None:
            if tag in self._SKIP:
                self._skip = max(0, self._skip - 1)

        def handle_data(self, data: str) -> None:
            if not self._skip:
                self._parts.append(data)

        def get_text(self) -> str:
            text = "".join(self._parts)
            text = html.unescape(text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text.strip()

    stripper = _Stripper()
    stripper.feed(raw_html)
    return stripper.get_text()


def _decode_header(value: str) -> str:
    """Decode an RFC 2047-encoded header value to a plain string."""
    parts = email.header.decode_header(value or "")
    result = []
    for fragment, charset in parts:
        if isinstance(fragment, bytes):
            result.append(fragment.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(fragment)
    return "".join(result)


def _extract_body(msg: email.message.Message) -> str:
    """Return best-effort plain-text body from a parsed email message."""
    plain = html_src = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and not plain:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    plain = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            elif ct == "text/html" and not html_src:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    html_src = payload.decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            plain = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return plain or (_strip_html(html_src) if html_src else "(no body)")


def _parse(raw: bytes) -> dict:
    """Parse raw email bytes into a structured dict."""
    msg = email.message_from_bytes(raw)
    return {
        "subject": _decode_header(msg.get("Subject", "")),
        "sender": _decode_header(msg.get("From", "")),
        "date": msg.get("Date", ""),
        "msg_id": msg.get("Message-ID", ""),
        "body": _extract_body(msg),
        "_msg": msg,
    }


def _preview(text: str, length: int = 120) -> str:
    snippet = text.replace("\n", " ").strip()
    return snippet[:length] + "…" if len(snippet) > length else snippet


SEP = "─" * 52


def _folder_name(entry: bytes) -> str:
    """Extract folder name from an IMAP LIST response entry.

    Entries end with a quoted name like ``("/" "INBOX"`` or an unquoted
    token.  Splitting on ``"`` gives an empty last element for quoted names,
    so we take the second-to-last segment in that case.
    """
    decoded = entry.decode()
    if decoded.endswith('"'):
        return decoded.rsplit('"', 2)[-2]
    return decoded.split()[-1]


@mcp.tool()
def read_inbox(count: int = 10, folder: str = "INBOX") -> str:
    """List recent emails from any IMAP folder.

    Args:
        count:  Number of recent emails to fetch (1–50, default 10).
        folder: IMAP folder to read (default "INBOX"). Use list_folders to see available names.

    Returns:
        Formatted list with ID, sender, subject, date, and a short preview.
    """
    try:
        count = max(1, min(count, 50))
        with _imap() as imap:
            imap.select(folder)
            _, data = imap.search(None, "ALL")
            ids = data[0].split()
            slice_ = list(reversed(ids[-count:]))

            lines = []
            for mid in slice_:
                _, fdata = imap.fetch(mid, "(RFC822)")
                parsed = _parse(fdata[0][1])
                lines.append(
                    f"ID: {mid.decode()}\n"
                    f"From: {parsed['sender']}\n"
                    f"Subject: {parsed['subject']}\n"
                    f"Date: {parsed['date']}\n"
                    f"Preview: {_preview(parsed['body'])}\n"
                    f"{SEP}"
                )

        if not lines:
            return f"{folder!r} is empty."
        return f"{folder} — {len(lines)} email(s):\n\n" + "\n\n".join(lines)
    except Exception as exc:
        return f"Error reading {folder!r}: {exc}"


@mcp.tool()
def read_folder(folder_name: str, count: int = 10) -> str:
    """List recent emails from a named IMAP folder.

    Args:
        folder_name: Folder to read (e.g. "LinkedIn", "GitHub", "Spam").
                     Use list_folders to see all available names.
        count:       Number of recent emails to fetch (1–50, default 10).

    Returns:
        Formatted list with ID, sender, subject, date, and a short preview.
    """
    try:
        count = max(1, min(count, 50))
        with _imap() as imap:
            imap.select(folder_name)
            _, data = imap.search(None, "ALL")
            ids = data[0].split()
            slice_ = list(reversed(ids[-count:]))

            lines = []
            for mid in slice_:
                _, fdata = imap.fetch(mid, "(RFC822)")
                parsed = _parse(fdata[0][1])
                lines.append(
                    f"ID: {mid.decode()}\n"
                    f"From: {parsed['sender']}\n"
                    f"Subject: {parsed['subject']}\n"
                    f"Date: {parsed['date']}\n"
                    f"Preview: {_preview(parsed['body'])}\n"
                    f"{SEP}"
                )

        if not lines:
            return f"{folder_name!r} is empty."
        return f"{folder_name} — {len(lines)} email(s):\n\n" + "\n\n".join(lines)
    except Exception as exc:
        return f"Error reading folder {folder_name!r}: {exc}"


@mcp.tool()
def read_email(message_id: str, folder: str = "INBOX") -> str:
    """Get the full content of a single email by its IMAP message ID.

    Args:
        message_id: Numeric IMAP message ID returned by read_inbox or search_emails.
        folder:     IMAP folder containing the email (default "INBOX").

    Returns:
        Full headers and body of the email.
    """
    try:
        with _imap() as imap:
            imap.select(folder)
            _, data = imap.fetch(message_id.encode(), "(RFC822)")
            if not data or data[0] is None:
                return f"Email ID {message_id!r} not found in {folder!r}."
            parsed = _parse(data[0][1])

        return (
            f"From: {parsed['sender']}\n"
            f"Subject: {parsed['subject']}\n"
            f"Date: {parsed['date']}\n"
            f"Message-ID: {parsed['msg_id']}\n"
            f"{SEP}\n"
            f"{parsed['body']}"
        )
    except Exception as exc:
        return f"Error reading email {message_id!r}: {exc}"


@mcp.tool()
def search_emails(
    query: str, search_in: str = "ALL", count: int = 10, folder: str = "INBOX"
) -> str:
    """Search any IMAP folder for emails matching a keyword.

    Args:
        query:     The search term.
        search_in: Field to search — ALL, FROM, SUBJECT, or BODY (default ALL).
        count:     Maximum results to return (1–50, default 10).
        folder:    IMAP folder to search (default "INBOX"). Use list_folders to see names.

    Returns:
        Matching emails with ID, sender, subject, and date.
    """
    try:
        count = max(1, min(count, 50))
        field = search_in.upper()
        if field not in {"ALL", "FROM", "SUBJECT", "BODY"}:
            return "search_in must be one of: ALL, FROM, SUBJECT, BODY"

        if field == "ALL":
            criteria = f'(OR (OR FROM "{query}" SUBJECT "{query}") BODY "{query}")'
        else:
            criteria = f'({field} "{query}")'

        with _imap() as imap:
            imap.select(folder)
            _, data = imap.search(None, criteria)
            ids = data[0].split()
            if not ids:
                return f"No emails found matching {query!r} in {folder!r}."

            slice_ = list(reversed(ids))[:count]
            lines = []
            for mid in slice_:
                _, fdata = imap.fetch(mid, "(RFC822)")
                parsed = _parse(fdata[0][1])
                lines.append(
                    f"ID: {mid.decode()}\n"
                    f"From: {parsed['sender']}\n"
                    f"Subject: {parsed['subject']}\n"
                    f"Date: {parsed['date']}\n"
                    f"{SEP}"
                )

        return (
            f"Found {len(ids)} match(es) for {query!r} in {folder!r} (showing {len(lines)}):\n\n"
            + "\n\n".join(lines)
        )
    except Exception as exc:
        return f"Error searching emails: {exc}"


@mcp.tool()
def send_email(to: str, subject: str, body: str) -> str:
    """Compose and send a new email via AOL SMTP.

    Args:
        to:      Recipient address(es), comma-separated for multiple.
        subject: Email subject line.
        body:    Plain-text email body.

    Returns:
        Success confirmation or a descriptive error message.
    """
    try:
        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = AOL_EMAIL
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))

        recipients = [addr.strip() for addr in to.split(",") if addr.strip()]
        with _smtp() as smtp:
            smtp.sendmail(AOL_EMAIL, recipients, msg.as_string())

        return f"Email sent to {to}."
    except Exception as exc:
        return f"Error sending email: {exc}"


@mcp.tool()
def reply_email(message_id: str, body: str, folder: str = "INBOX") -> str:
    """Reply to an existing email by its IMAP message ID.

    Args:
        message_id: Numeric IMAP message ID of the email to reply to.
        body:       Plain-text reply body.
        folder:     IMAP folder containing the original email (default "INBOX").

    Returns:
        Success confirmation or a descriptive error message.
    """
    try:
        with _imap() as imap:
            imap.select(folder)
            _, data = imap.fetch(message_id.encode(), "(RFC822)")
            if not data or data[0] is None:
                return f"Email ID {message_id!r} not found."
            original = email.message_from_bytes(data[0][1])

        sender = original.get("From", "")
        orig_subj = _decode_header(original.get("Subject", ""))
        orig_msg_id = original.get("Message-ID", "")
        reply_subj = orig_subj if orig_subj.startswith("Re:") else f"Re: {orig_subj}"

        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = AOL_EMAIL
        msg["To"] = sender
        msg["Subject"] = reply_subj
        msg["In-Reply-To"] = orig_msg_id
        msg["References"] = orig_msg_id
        msg.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))

        with _smtp() as smtp:
            smtp.sendmail(AOL_EMAIL, [sender], msg.as_string())

        return f"Reply sent to {sender}."
    except Exception as exc:
        return f"Error replying to email {message_id!r}: {exc}"


@mcp.tool()
def delete_email(message_id: str, folder: str = "INBOX") -> str:
    """Move an email to the Trash folder by its IMAP message ID.

    Args:
        message_id: Numeric IMAP message ID of the email to delete.
        folder:     IMAP folder containing the email (default "INBOX").

    Returns:
        Confirmation of deletion or a descriptive error message.
    """
    try:
        with _imap() as imap:
            imap.select(folder)

            _, folder_list = imap.list()
            folder_names = [_folder_name(e) for e in (folder_list or []) if e]

            trash = next(
                (f for f in ("Trash", "Deleted Items", "Deleted Messages") if f in folder_names),
                "Trash",
            )

            status, _ = imap.copy(message_id.encode(), trash)
            if status != "OK":
                return f"Could not copy email {message_id!r} to {trash!r}."

            imap.store(message_id.encode(), "+FLAGS", "\\Deleted")
            imap.expunge()

        return f"Email {message_id} moved to {trash!r}."
    except Exception as exc:
        return f"Error deleting email {message_id!r}: {exc}"


@mcp.tool()
def delete_all_in_folder(folder_name: str) -> str:
    """Delete all emails in a folder by moving them to Trash.

    Args:
        folder_name: Folder to clear (e.g. "Spam", "LinkedIn", "Bulk Mail").
                     Use list_folders to see available names.

    Returns:
        Summary of how many emails were deleted or a descriptive error message.
    """
    try:
        with _imap() as imap:
            imap.select(folder_name)

            _, folder_list = imap.list()
            folder_names = [_folder_name(e) for e in (folder_list or []) if e]
            trash = next(
                (f for f in ("Trash", "Deleted Items", "Deleted Messages") if f in folder_names),
                "Trash",
            )

            _, data = imap.uid("SEARCH", None, "ALL")
            uids = data[0].split()
            if not uids:
                return f"{folder_name!r} is already empty."

            uid_set = b",".join(uids)
            status, _ = imap.uid("COPY", uid_set, trash)
            if status != "OK":
                return f"Could not copy emails to {trash!r}."

            imap.uid("STORE", uid_set, "+FLAGS", "\\Deleted")
            imap.expunge()

        return f"Deleted {len(uids)} email(s) from {folder_name!r}."
    except Exception as exc:
        return f"Error deleting emails in {folder_name!r}: {exc}"


@mcp.tool()
def move_email(message_id: str, folder: str, source_folder: str = "INBOX") -> str:
    """Move an email to any named IMAP folder.

    Args:
        message_id:    Numeric IMAP message ID of the email to move.
        folder:        Destination folder name (use list_folders to see available names).
        source_folder: Folder currently containing the email (default "INBOX").

    Returns:
        Confirmation or a descriptive error message.
    """
    try:
        with _imap() as imap:
            imap.select(source_folder)
            status, _ = imap.copy(message_id.encode(), folder)
            if status != "OK":
                return (
                    f"Could not copy email {message_id!r} to {folder!r}. "
                    "Use list_folders to verify the folder name."
                )
            imap.store(message_id.encode(), "+FLAGS", "\\Deleted")
            imap.expunge()

        return f"Email {message_id} moved to {folder!r}."
    except Exception as exc:
        return f"Error moving email {message_id!r}: {exc}"


@mcp.tool()
def move_all_emails(source_folder: str, destination_folder: str) -> str:
    """Move all emails from one folder to another.

    Args:
        source_folder:      Folder to move emails from (e.g. "LinkedIn", "Spam").
        destination_folder: Folder to move emails into (e.g. "INBOX", "Archive").
                            Use list_folders to see available names.

    Returns:
        Summary of how many emails were moved or a descriptive error message.
    """
    try:
        with _imap() as imap:
            imap.select(source_folder)
            _, data = imap.uid("SEARCH", None, "ALL")
            uids = data[0].split()
            if not uids:
                return f"{source_folder!r} is empty — nothing to move."

            uid_set = b",".join(uids)
            status, _ = imap.uid("COPY", uid_set, destination_folder)
            if status != "OK":
                return (
                    f"Could not copy emails to {destination_folder!r}. "
                    "Use list_folders to verify the folder name."
                )

            imap.uid("STORE", uid_set, "+FLAGS", "\\Deleted")
            imap.expunge()

        return f"Moved {len(uids)} email(s) from {source_folder!r} to {destination_folder!r}."
    except Exception as exc:
        return f"Error moving emails from {source_folder!r} to {destination_folder!r}: {exc}"


@mcp.tool()
def list_folders() -> str:
    """List all IMAP folders available in the AOL mailbox.

    Returns:
        Bullet list of every folder name in the account.
    """
    try:
        with _imap() as imap:
            _, folder_list = imap.list()

        names = [_folder_name(e) for e in (folder_list or []) if e]

        if not names:
            return "No folders found."
        return "Available folders:\n" + "\n".join(f"  • {n}" for n in names)
    except Exception as exc:
        return f"Error listing folders: {exc}"


@mcp.tool()
def mark_read(message_ids: str, folder: str = "INBOX") -> str:
    """Mark one or more emails as read.

    Args:
        message_ids: Single IMAP message ID or comma-separated list (e.g. "42,43,44").
        folder:      IMAP folder containing the emails (default "INBOX").

    Returns:
        Summary of which IDs were marked and any failures.
    """
    try:
        ids = [mid.strip() for mid in message_ids.split(",") if mid.strip()]
        if not ids:
            return "No message IDs provided."

        ok: list[str] = []
        failed: list[str] = []
        with _imap() as imap:
            imap.select(folder)
            for mid in ids:
                status, _ = imap.store(mid.encode(), "+FLAGS", "\\Seen")
                (ok if status == "OK" else failed).append(mid)

        parts = []
        if ok:
            parts.append(f"Marked as read: {', '.join(ok)}")
        if failed:
            parts.append(f"Failed: {', '.join(failed)}")
        return ". ".join(parts) + "."
    except Exception as exc:
        return f"Error marking emails as read: {exc}"


@mcp.tool()
def get_attachments(message_id: str, folder: str = "INBOX") -> str:
    """List all attachments in an email by its IMAP message ID.

    Args:
        message_id: Numeric IMAP message ID of the email to inspect.
        folder:     IMAP folder containing the email (default "INBOX").

    Returns:
        Each attachment's filename, MIME type, and size, or a 'no attachments' notice.
    """
    try:
        with _imap() as imap:
            imap.select(folder)
            _, data = imap.fetch(message_id.encode(), "(RFC822)")
            if not data or data[0] is None:
                return f"Email ID {message_id!r} not found."
            msg = email.message_from_bytes(data[0][1])

        attachments = []
        for part in msg.walk():
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" not in disposition:
                continue

            filename = _decode_header(part.get_filename() or "") or "unnamed"
            payload = part.get_payload(decode=True)
            size = len(payload) if payload else 0

            if size < 1024:
                size_str = f"{size} B"
            elif size < 1024**2:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size / 1024**2:.1f} MB"

            attachments.append(f"  • {filename}  [{part.get_content_type()}]  {size_str}")

        if not attachments:
            return f"No attachments found in email {message_id}."
        return f"Attachments in email {message_id}:\n" + "\n".join(attachments)
    except Exception as exc:
        return f"Error getting attachments for email {message_id!r}: {exc}"


def main() -> None:
    """Start the MCP server."""
    if not AOL_EMAIL or not AOL_APP_PASSWORD:
        raise SystemExit(
            "AOL_EMAIL and AOL_APP_PASSWORD must be set.\n"
            "Copy .env.example to .env and fill in your credentials."
        )
    mcp.run()


if __name__ == "__main__":
    main()
