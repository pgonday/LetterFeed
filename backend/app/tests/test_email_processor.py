import imaplib
from email.message import Message
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from app.crud.newsletters import create_newsletter
from app.crud.settings import create_or_update_settings
from app.models.newsletters import Newsletter
from app.schemas.newsletters import NewsletterCreate
from app.schemas.settings import Settings, SettingsCreate
from app.services.email_processor import (
    _clean_display_name,
    _extract_sender,
    _process_single_email,
    process_emails,
)
from app.tests.conftest import set_uid_responses


def _setup_test_email_processing(
    db_session: Session,
    newsletter_create_data: NewsletterCreate,
    settings_create_data: SettingsCreate,
) -> tuple[MagicMock, Newsletter, Settings]:
    """Help to set up mocks and data for email processing tests."""
    settings = create_or_update_settings(db_session, settings_create_data)
    newsletter = create_newsletter(db_session, newsletter_create_data)

    mock_mail = MagicMock(spec=imaplib.IMAP4_SSL)
    msg = Message()
    msg["From"] = newsletter_create_data.sender_emails[0]
    msg["Subject"] = "Test Email"
    msg["Message-ID"] = "<test-message-id>"
    msg.set_payload("<html><body><p>Original Body</p></body></html>", "utf-8")
    set_uid_responses(mock_mail, msg.as_bytes())

    return mock_mail, newsletter, settings


def test_process_single_email_with_newsletter_move_folder(db_session: Session):
    """Test that the per-newsletter move_to_folder is used, overriding the global setting."""
    # 1. ARRANGE
    settings_data = SettingsCreate(
        imap_server="test.com",
        imap_username="test",
        imap_password="password",
        move_to_folder="GlobalArchive",
    )
    newsletter_data = NewsletterCreate(
        name="Test Newsletter",
        sender_emails=["test@example.com"],
        move_to_folder="NewsletterArchive",
    )
    mock_mail, newsletter, settings = _setup_test_email_processing(
        db_session, newsletter_data, settings_data
    )
    sender_map = {newsletter.senders[0].email: newsletter}

    # 2. ACT
    _process_single_email("1", mock_mail, db_session, sender_map, settings)

    # 3. ASSERT
    mock_mail.uid.assert_any_call("COPY", "1", "NewsletterArchive")
    mock_mail.uid.assert_any_call("STORE", "1", "+FLAGS", "\\Deleted")


def test_process_single_email_with_global_move_folder(db_session: Session):
    """Test that the global move_to_folder is used when the per-newsletter one is not set."""
    # 1. ARRANGE
    settings_data = SettingsCreate(
        imap_server="test.com",
        imap_username="test",
        imap_password="password",
        move_to_folder="GlobalArchive",
    )
    newsletter_data = NewsletterCreate(
        name="Test Newsletter", sender_emails=["test@example.com"]
    )
    mock_mail, newsletter, settings = _setup_test_email_processing(
        db_session, newsletter_data, settings_data
    )
    sender_map = {newsletter.senders[0].email: newsletter}

    # 2. ACT
    _process_single_email("1", mock_mail, db_session, sender_map, settings)

    # 3. ASSERT
    mock_mail.uid.assert_any_call("COPY", "1", "GlobalArchive")
    mock_mail.uid.assert_any_call("STORE", "1", "+FLAGS", "\\Deleted")


@patch("app.services.email_processor._connect_to_imap")
def test_process_emails_uses_newsletter_search_folder(
    mock_connect_to_imap,
    db_session: Session,
):
    """Test that the per-newsletter search_folder is used, overriding the global setting."""
    # 1. ARRANGE
    settings_data = SettingsCreate(
        imap_server="test.com",
        imap_username="test",
        imap_password="password",
        search_folder="GlobalInbox",
    )
    create_or_update_settings(db_session, settings_data)

    newsletter_data = NewsletterCreate(
        name="Test Newsletter",
        sender_emails=["test@example.com"],
        search_folder="NewsletterInbox",
    )
    create_newsletter(db_session, newsletter_data)

    # Mock the return of _connect_to_imap to avoid a real IMAP connection
    mock_connect_to_imap.return_value = None

    # 2. ACT
    process_emails(db_session)

    # 3. ASSERT
    # Check that _connect_to_imap was called with the newsletter's specific folder
    mock_connect_to_imap.assert_called_once()
    call_args = mock_connect_to_imap.call_args[0]
    assert call_args[1] == "NewsletterInbox"


@patch("app.services.email_processor._connect_to_imap")
def test_process_emails_uses_global_search_folder(
    mock_connect_to_imap,
    db_session: Session,
):
    """Test that the global search_folder is used when the per-newsletter one is not set."""
    # 1. ARRANGE
    settings_data = SettingsCreate(
        imap_server="test.com",
        imap_username="test",
        imap_password="password",
        search_folder="GlobalInbox",
    )
    create_or_update_settings(db_session, settings_data)

    newsletter_data = NewsletterCreate(
        name="Test Newsletter",
        sender_emails=["test@example.com"],
        search_folder=None,  # Explicitly not set
    )
    create_newsletter(db_session, newsletter_data)

    mock_connect_to_imap.return_value = None

    # 2. ACT
    process_emails(db_session)

    # 3. ASSERT
    mock_connect_to_imap.assert_called_once()
    call_args = mock_connect_to_imap.call_args[0]
    assert call_args[1] == "GlobalInbox"


@patch("app.services.email_processor._extract_and_clean_html")
def test_process_single_email_with_content_extraction(
    mock_extract_clean,
    db_session: Session,
):
    """Test that the cleaning function is called when extract_content is True."""
    # 1. ARRANGE
    mock_extract_clean.return_value = {
        "title": "Extracted Title",
        "body": "Extracted Body",
    }
    settings_data = SettingsCreate(
        imap_server="test.com", imap_username="test", imap_password="password"
    )
    newsletter_data = NewsletterCreate(
        name="Test Newsletter",
        sender_emails=["test@example.com"],
        extract_content=True,
    )
    mock_mail, newsletter, settings = _setup_test_email_processing(
        db_session, newsletter_data, settings_data
    )
    sender_map = {newsletter.senders[0].email: newsletter}

    # 2. ACT
    with patch("app.services.email_processor.create_entry") as mock_create_entry:
        _process_single_email("1", mock_mail, db_session, sender_map, settings)

    # 3. ASSERT
    mock_extract_clean.assert_called_once()
    # Check that create_entry was called with the extracted body
    mock_create_entry.assert_called_once()
    entry_create_arg = mock_create_entry.call_args[0][1]
    assert entry_create_arg.body == "Extracted Body"
    # Subject should still come from the email, not the extracted title
    assert entry_create_arg.subject == "Test Email"


def test_process_single_email_with_encoded_from_header(db_session: Session):
    """Test that an encoded From header is correctly decoded for the newsletter name."""
    # 1. ARRANGE
    settings_data = SettingsCreate(
        imap_server="test.com",
        imap_username="test",
        imap_password="password",
        auto_add_new_senders=True,
    )
    settings = create_or_update_settings(db_session, settings_data)

    mock_mail = MagicMock(spec=imaplib.IMAP4_SSL)
    msg = Message()
    # "Кирилл" in Cyrillic, base64 encoded for UTF-8
    from_header = "=?utf-8?B?0JrQuNGA0LjQu9C7?= <test@example.com>"
    msg["From"] = from_header
    msg["Subject"] = "Test Email"
    msg["Message-ID"] = "<test-message-id-encoded-from>"
    msg.set_payload("<html><body><p>Body</p></body></html>", "utf-8")
    set_uid_responses(mock_mail, msg.as_bytes())

    sender_map = {}  # empty, to trigger auto-add

    # 2. ACT
    _process_single_email("1", mock_mail, db_session, sender_map, settings)

    # 3. ASSERT
    from app.crud.newsletters import get_newsletters

    newsletters = get_newsletters(db_session)
    assert len(newsletters) == 1
    assert newsletters[0].name == "Кирилл"
    assert newsletters[0].senders[0].email == "test@example.com"


def test_process_single_email_with_null_bytes_in_body(db_session: Session):
    """Test that an email with NULL bytes in its body is handled gracefully.

    - The NULL bytes should be stripped.
    - Content extraction should still be attempted.
    - If it fails, an error is logged and the raw body is used.
    """
    # 1. ARRANGE
    settings_data = SettingsCreate(
        imap_server="test.com", imap_username="test", imap_password="password"
    )
    newsletter_data = NewsletterCreate(
        name="Test Newsletter",
        sender_emails=["test@example.com"],
        extract_content=True,  # Important: we want to test the extraction path
    )
    settings = create_or_update_settings(db_session, settings_data)
    newsletter = create_newsletter(db_session, newsletter_data)

    mock_mail = MagicMock(spec=imaplib.IMAP4_SSL)
    msg = Message()
    msg["From"] = "test@example.com"
    msg["Subject"] = "Test Email with NULLs"
    msg["Message-ID"] = "<test-message-id-nulls>"
    # The body contains NULL bytes that would cause readability-lxml to crash
    body_with_nulls = "<html><body><p>Hello\x00 World</p></body></html>"
    msg.set_payload(body_with_nulls, "utf-8")
    set_uid_responses(mock_mail, msg.as_bytes())

    sender_map = {newsletter.senders[0].email: newsletter}

    # 2. ACT & ASSERT
    with (
        patch("app.services.email_processor.logger") as mock_logger,
        patch("app.services.email_processor.create_entry") as mock_create_entry,
    ):
        # We mock readability.Document to simulate a failure *after* our sanitization
        # to ensure the try/except block is also working.
        with patch("app.services.email_processor.Document") as mock_document:
            mock_document.side_effect = ValueError("Simulated lxml failure")
            _process_single_email("1", mock_mail, db_session, sender_map, settings)

            # Assert that the logger was called with a warning
            mock_logger.warning.assert_called_once()
            assert "Failed to extract content" in mock_logger.warning.call_args[0][0]

        # Check that an entry was still created
        mock_create_entry.assert_called_once()
        entry_create_arg = mock_create_entry.call_args[0][1]

        # The body should be the original (but decoded) body, since extraction failed
        # Note: _get_email_body will decode the payload.
        assert "Hello\x00 World" in entry_create_arg.body


def _sender_header_config(header: str):
    """Stand in for the frozen env config, which cannot be patched in place."""
    return patch(
        "app.services.email_processor.env_config",
        SimpleNamespace(sender_header=header),
    )


def test_clean_display_name_strips_alias_suffix():
    """The alias suffix appended by forwarding services is removed."""
    assert (
        _clean_display_name("Example News 'contact at example.com'") == "Example News"
    )
    # Names that do not carry the suffix are left untouched.
    assert _clean_display_name("Example Daily") == "Example Daily"
    # A name made only of the suffix falls back to the original string.
    assert _clean_display_name("'info at example.com'") == "'info at example.com'"


def test_extract_sender_prefers_configured_header():
    """The configured header wins over a rewritten 'From'."""
    msg = Message()
    msg["From"] = (
        "\"Example News 'contact at example.com'\" "
        "<a1b2c3d4-0000-4000-8000-000000000000+contact=example.com@alias.example>"
    )
    msg["X-AnonAddy-Original-Sender"] = "contact@example.com"

    with _sender_header_config("X-AnonAddy-Original-Sender"):
        assert _extract_sender(msg) == "contact@example.com"


def test_extract_sender_falls_back_to_from_header():
    """'From' is used when the header is unset, absent or unusable."""
    msg = Message()
    msg["From"] = "Example Daily <news@example.org>"

    # No header configured: current behaviour is preserved.
    with _sender_header_config(""):
        assert _extract_sender(msg) == "news@example.org"

    # Configured but absent from the message.
    with _sender_header_config("X-AnonAddy-Original-Sender"):
        assert _extract_sender(msg) == "news@example.org"

    # Present but holding no usable address.
    msg["X-AnonAddy-Original-Sender"] = "not an address"
    with _sender_header_config("X-AnonAddy-Original-Sender"):
        assert _extract_sender(msg) == "news@example.org"
