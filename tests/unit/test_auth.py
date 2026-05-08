import pytest
from datetime import timedelta
from app.security.auth import (
    create_access_token,
    decode_access_token,
    verify_password,
    get_password_hash,
    RoleChecker,
    check_permission,
    ROLES,
    PERMISSIONS
)

def test_password_hashing():
    password = "test_password_123"
    hashed = get_password_hash(password)
    assert verify_password(password, hashed)
    assert not verify_password("wrong_password", hashed)

def test_jwt_token_creation_and_decoding():
    data = {"sub": "test_user", "user_info": {"username": "test_user", "role": "ULTRASOUND_DOCTOR"}}
    token = create_access_token(data, expires_delta=timedelta(minutes=1))
    
    decoded = decode_access_token(token)
    assert decoded["sub"] == "test_user"
    assert decoded["user_info"]["username"] == "test_user"
    assert decoded["user_info"]["role"] == "ULTRASOUND_DOCTOR"

def test_role_checker():
    checker = RoleChecker(["ULTRASOUND_DOCTOR", "DEPARTMENT_HEAD"])

    user1 = {"role": "ULTRASOUND_DOCTOR", "username": "doctor"}
    user2 = {"role": "DEPARTMENT_HEAD", "username": "head"}
    user3 = {"role": "SYSTEM_ADMIN", "username": "admin"}
    user4 = {"role": "EMERGENCY_DOCTOR", "username": "emergency"}

    assert checker(user1) is True
    assert checker(user2) is True

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excinfo:
        checker(user3)
    assert excinfo.value.status_code == 403

    with pytest.raises(HTTPException) as excinfo:
        checker(user4)
    assert excinfo.value.status_code == 403

def test_check_permission():
    user_doctor = {"role": "ULTRASOUND_DOCTOR"}
    user_head = {"role": "DEPARTMENT_HEAD"}
    user_admin = {"role": "SYSTEM_ADMIN"}
    user_emergency = {"role": "EMERGENCY_DOCTOR"}
    
    assert check_permission("submit_diagnosis", user_doctor) is True
    assert check_permission("view_results", user_doctor) is True
    assert check_permission("review_l3", user_doctor) is False
    
    assert check_permission("review_l3", user_head) is True
    assert check_permission("configure_thresholds", user_head) is True
    
    assert check_permission("submit_diagnosis", user_admin) is True
    assert check_permission("review_l3", user_admin) is True
    assert check_permission("view_audit_logs", user_admin) is True
    
    assert check_permission("submit_feedback", user_emergency) is False

def test_roles_and_permissions_structure():
    assert len(ROLES) == 5
    assert "ULTRASOUND_DOCTOR" in ROLES
    assert "SYSTEM_ADMIN" in ROLES
    
    assert len(PERMISSIONS) == 5
    assert "ULTRASOUND_DOCTOR" in PERMISSIONS
    assert "SYSTEM_ADMIN" in PERMISSIONS
    assert "*" in PERMISSIONS["SYSTEM_ADMIN"]