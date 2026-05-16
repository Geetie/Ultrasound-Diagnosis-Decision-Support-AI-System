import json
from typing import List, Optional

import structlog

from .models import AlertContact

_logger = structlog.get_logger("app.observability.alert_escalation.contacts")


def load_contacts(contacts_json: str) -> List[AlertContact]:
    if not contacts_json:
        return []
    try:
        contacts_data = json.loads(contacts_json)
        contacts = [_build_contact(cd) for cd in contacts_data]
        _logger.info("alert_contacts_loaded", count=len(contacts))
        return contacts
    except (json.JSONDecodeError, TypeError) as e:
        _logger.error("alert_contacts_parse_failed", error=str(e))
        return []


def parse_contacts(contacts_json: str) -> Optional[List[AlertContact]]:
    try:
        contacts_data = json.loads(contacts_json)
        return [_build_contact(cd) for cd in contacts_data]
    except (json.JSONDecodeError, TypeError) as e:
        _logger.error("update_contacts_parse_failed", error=str(e))
        return None


def _build_contact(cd: dict) -> AlertContact:
    role = cd.get("role", "")
    contact = AlertContact(
        name=cd.get("name", ""),
        role=role,
        phone=cd.get("phone", ""),
        email=cd.get("email", ""),
        superior_hospital_ops=cd.get("superior_hospital_ops", ""),
        hospital_admin_duty=cd.get("hospital_admin_duty", ""),
        equipment_supplier=cd.get("equipment_supplier", ""),
    )
    _ROLE_FIELD_MAP = {
        "superior_hospital_ops": "superior_hospital_ops",
        "hospital_admin_duty": "hospital_admin_duty",
        "equipment_supplier": "equipment_supplier",
    }
    if role in _ROLE_FIELD_MAP and not getattr(contact, _ROLE_FIELD_MAP[role]):
        setattr(contact, _ROLE_FIELD_MAP[role], cd.get("name", ""))
    return contact


async def verify_contact_reachability(
    webhook_url: str, contacts: List[AlertContact]
) -> bool:
    if not webhook_url:
        _logger.warning("contact_reachability_skip_no_webhook")
        return False
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                webhook_url,
                json={"type": "reachability_check", "contact_count": len(contacts)},
            )
            return resp.status_code < 500
    except Exception as e:
        _logger.error("contact_reachability_check_failed", error=str(e))
        return False
