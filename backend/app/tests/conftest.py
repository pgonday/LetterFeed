import os

os.environ["LETTERFEED_DATABASE_URL"] = "sqlite:///./test.db"
os.environ["LETTERFEED_SECRET_KEY"] = "testsecret"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, engine, get_db
from app.main import app

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function", autouse=True)
def setup_and_teardown_db():
    """Set up and tear down the database for each test.

    This fixture is automatically used for all tests. It creates all tables
    before a test and drops them afterwards. This ensures a clean database for
    every test.
    """
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="db_session")
def db_session_fixture():
    """Yield a database session for a test."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(name="client")
def client_fixture(db_session):
    """Yield a TestClient for a test."""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_db(request):
    """Clean up the test database after the test session.

    This fixture is automatically used once per test session. It registers a
    finalizer to remove the test database file after all tests have run.
    """

    def remove_test_db():
        # The path is relative to the backend directory where pytest is run
        db_file = "test.db"
        if os.path.exists(db_file):
            os.remove(db_file)

    request.addfinalizer(remove_test_db)


def set_uid_responses(mock_mail, message_bytes, uid=b"1"):
    """Route mock_mail.uid() by IMAP command, the way imaplib dispatches UID.

    SEARCH answers with a single UID, FETCH answers with the given message, and
    every other command (STORE, COPY) simply succeeds.
    """

    def _uid(command, *args):
        command = command.upper()
        if command == "SEARCH":
            return ("OK", [uid])
        if command == "FETCH":
            return ("OK", [(b"1 (RFC822)", message_bytes)])
        return ("OK", [b""])

    mock_mail.uid.side_effect = _uid
    return mock_mail
