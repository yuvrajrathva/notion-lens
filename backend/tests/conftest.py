import uuid

import pytest

from db import SessionLocal
from models import AppUser


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def make_app_user(db_session):
    """Creates a throwaway app_users row (and cascades away everything hung off
    it) so tests don't leak data into the shared dev database."""
    created_ids = []

    def _make(email: str | None = None) -> uuid.UUID:
        user_id = uuid.uuid4()
        email = email or f"test-{user_id}@example.com"
        db_session.add(AppUser(id=user_id, email=email))
        db_session.commit()
        created_ids.append(user_id)
        return user_id

    yield _make

    for user_id in created_ids:
        user = db_session.get(AppUser, user_id)
        if user:
            db_session.delete(user)
    db_session.commit()
