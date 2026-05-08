import pytest
from datetime import datetime, timedelta, timezone
from freezegun import freeze_time
from app.security.refresh_token import (
    RefreshTokenStore,
    create_refresh_token,
    verify_refresh_token,
    revoke_refresh_token,
    revoke_token_family,
    revoke_all_user_tokens,
    cleanup_expired_tokens,
    get_refresh_token_count
)


@pytest.fixture(autouse=True)
def reset_refresh_store():
    store = RefreshTokenStore()
    store._store.clear()
    store._family_store.clear()
    yield
    store._store.clear()
    store._family_store.clear()


@pytest.fixture
def sample_user_info():
    return {
        "username": "test_user",
        "full_name": "Test User",
        "email": "test@example.com",
        "role": "ULTRASOUND_DOCTOR",
        "disabled": False
    }


def test_create_refresh_token(sample_user_info):
    token_id = create_refresh_token("user123", sample_user_info)

    assert token_id is not None
    assert len(token_id) > 20


def test_verify_valid_refresh_token(sample_user_info):
    token_id = create_refresh_token("user123", sample_user_info)

    token_data = verify_refresh_token(token_id)

    assert token_data is not None
    assert token_data["user_id"] == "user123"
    assert token_data["user_info"]["username"] == "test_user"
    assert token_data["revoked"] is False


def test_verify_invalid_refresh_token():
    token_data = verify_refresh_token("invalid_token_id_12345")

    assert token_data is None


def test_revoke_refresh_token(sample_user_info):
    token_id = create_refresh_token("user123", sample_user_info)

    success = revoke_refresh_token(token_id)
    assert success is True

    token_data = verify_refresh_token(token_id)
    assert token_data is None


def test_revoke_nonexistent_token():
    success = revoke_refresh_token("nonexistent_token_12345")

    assert success is False


def test_token_family_rotation(sample_user_info):
    family_id = "test_family_123"

    token1 = create_refresh_token("user123", sample_user_info, family_id=family_id)
    token2 = create_refresh_token("user123", sample_user_info, family_id=family_id)

    revoked_count = revoke_token_family(family_id)

    assert revoked_count == 2

    assert verify_refresh_token(token1) is None
    assert verify_refresh_token(token2) is None


def test_revoke_all_user_tokens(sample_user_info):
    token1 = create_refresh_token("user123", sample_user_info)
    token2 = create_refresh_token("user123", sample_user_info)
    token3 = create_refresh_token("user456", sample_user_info)

    revoked_count = revoke_all_user_tokens("user123")

    assert revoked_count == 2

    assert verify_refresh_token(token1) is None
    assert verify_refresh_token(token2) is None
    assert verify_refresh_token(token3) is not None


def test_token_family_inheritance(sample_user_info):
    token1 = create_refresh_token("user123", sample_user_info)

    token1_data = verify_refresh_token(token1)
    family_id = token1_data["family_id"]

    token2 = create_refresh_token("user123", sample_user_info, family_id=family_id)

    assert token2 is not None
    token2_data = verify_refresh_token(token2)
    assert token2_data["family_id"] == family_id


def test_get_refresh_token_count(sample_user_info):
    token1 = create_refresh_token("user123", sample_user_info)
    token2 = create_refresh_token("user456", sample_user_info)

    stats = get_refresh_token_count()

    assert stats["total"] == 2
    assert stats["active"] == 2
    assert stats["revoked"] == 0
    assert stats["expired"] == 0


def test_get_token_count_after_revocation(sample_user_info):
    token1 = create_refresh_token("user123", sample_user_info)
    token2 = create_refresh_token("user456", sample_user_info)

    revoke_refresh_token(token1)

    stats = get_refresh_token_count()

    assert stats["total"] == 2
    assert stats["active"] == 1
    assert stats["revoked"] == 1
    assert stats["expired"] == 0


def test_token_store_singleton():
    store1 = RefreshTokenStore()
    store2 = RefreshTokenStore()

    assert store1 is store2


def test_refresh_token_contains_required_fields(sample_user_info):
    token_id = create_refresh_token("user123", sample_user_info)
    token_data = verify_refresh_token(token_id)

    assert "token_id" in token_data
    assert "user_id" in token_data
    assert "user_info" in token_data
    assert "family_id" in token_data
    assert "created_at" in token_data
    assert "expires_at" in token_data
    assert "revoked" in token_data
    assert "last_used_at" in token_data


def test_refresh_token_expiry_handling(sample_user_info):
    with freeze_time("2026-01-01 00:00:00"):
        token_id = create_refresh_token("user123", sample_user_info)

    with freeze_time("2026-01-10 00:00:00"):
        token_data = verify_refresh_token(token_id)

        assert token_data is None


def test_cleanup_expired_tokens(sample_user_info):
    with freeze_time("2026-01-01 00:00:00"):
        expired_token = create_refresh_token("user123", sample_user_info)
    
    with freeze_time("2026-01-08 00:00:00"):
        active_token = create_refresh_token("user456", sample_user_info)
    
    with freeze_time("2026-01-10 00:00:00"):
        cleaned_count = cleanup_expired_tokens()
        store = RefreshTokenStore()
        
        assert cleaned_count == 1
        assert expired_token not in store._store
        assert active_token in store._store
    
    store = RefreshTokenStore()
    store._store.clear()
    store._family_store.clear()


def test_concurrent_token_creation(sample_user_info):
    import threading

    results = []
    errors = []

    def create_token():
        try:
            token = create_refresh_token("user123", sample_user_info)
            results.append(token)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=create_token) for _ in range(10)]

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(results) == 10


def test_revoke_already_revoked_token(sample_user_info):
    token_id = create_refresh_token("user123", sample_user_info)

    revoke_refresh_token(token_id)
    result = revoke_refresh_token(token_id)

    assert result is True


def test_refresh_token_updates_last_used(sample_user_info):
    token_id = create_refresh_token("user123", sample_user_info)

    token_data1 = verify_refresh_token(token_id)
    last_used1 = token_data1["last_used_at"]

    token_data2 = verify_refresh_token(token_id)
    last_used2 = token_data2["last_used_at"]

    assert last_used2 >= last_used1