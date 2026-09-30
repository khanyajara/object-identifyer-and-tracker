"""Stable account identity, independent of detected driver identity."""
from uuid import NAMESPACE_URL, uuid5


def account_uid(account):
    existing = account.get("uid") or account.get("user_id")
    if existing:
        if str(existing) in {".", ".."}:
            raise ValueError("Invalid account UID")
        return str(existing)
    username = str(account.get("username") or "").strip().casefold()
    if not username:
        raise ValueError("A signed-in account is required to record video.")
    # Legacy accounts have username primary keys. Derive the same UID locally
    # and in cloud mode without assigning a fresh owner on every sign-in.
    return str(uuid5(NAMESPACE_URL, "urn:roadwatch:account:" + username))


def account_identity(account):
    uid = account_uid(account)
    return {"username": account["username"], "role": account["role"], "uid": uid, "user_id": uid}


def video_owner(record):
    uid = record.get("user_id") or record.get("uid")
    if not isinstance(uid, str) or not uid.strip():
        raise ValueError("Recording owner is missing. Do not assign legacy videos to the current user.")
    if record.get("uid") and record.get("user_id") and record["uid"] != record["user_id"]:
        raise ValueError("Recording UID and user_id disagree.")
    if uid in {".", ".."}:
        raise ValueError("Invalid recording owner")
    return uid


def can_access_video(record, actor):
    if actor.get("role") in {"admin", "super_admin"}:
        return True
    return bool(record.get("user_id") and record["user_id"] == account_uid(actor))
