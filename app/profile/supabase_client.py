"""
Supabase REST client for profile & business data with unified ID resolution.
"""
import json
import logging
import uuid
from typing import Any, Optional

import httpx

from app.core.config import supabase_url, supabase_anon_key, supabase_admin_config

logger = logging.getLogger(__name__)

SUPABASE_URL = supabase_url()
SUPABASE_KEY = supabase_anon_key()

# ── Anon-key headers (read-only / public ops) ──
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

# ── Service-role headers (bypasses RLS — used for admin upserts) ──
_svc_url, _svc_key = supabase_admin_config()
SERVICE_HEADERS = {
    "apikey": _svc_key,
    "Authorization": f"Bearer {_svc_key}",
    "Content-Type": "application/json",
}

VALID_BUSINESS_COLUMNS = {
    "legal_name",
    "phone_number",
    "physical_address",
    "city",
    "region",
    "latitude",
    "longitude",
    "industry_group",
    "logo_url",
}


def _url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"


# ---- users_table helpers ----


def _normalize_uuid_str(raw_id: str) -> str:
    """Normalize a 32-char hex string to hyphenated UUID when valid."""
    val = (raw_id or "").strip()
    if not val:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(val))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, val))


async def _ensure_auth_user(
    client: httpx.AsyncClient,
    target_id: str,
    email: str,
    full_name: str,
) -> str:
    """
    Ensure a matching record exists in Supabase `auth.users` before inserting into
    `public.users_table` (which has FK `users_table_id_fkey -> auth.users(id)`).
    Returns the confirmed `auth.users.id` UUID string.
    """
    auth_headers = {
        "apikey": _svc_key,
        "Authorization": f"Bearer {_svc_key}",
        "Content-Type": "application/json",
    }

    # 1. Try creating the user in Supabase Auth with our desired UUID
    try:
        create_auth_resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            headers=auth_headers,
            json={
                "id": target_id,
                "email": email,
                "email_confirm": True,
                "user_metadata": {"full_name": full_name},
            },
        )
        if create_auth_resp.status_code in (200, 201):
            body = create_auth_resp.json()
            resolved = body.get("id") or (body.get("user") or {}).get("id")
            if resolved:
                return str(resolved)
    except Exception as exc:
        logger.debug(f"_ensure_auth_user create error: {exc}")

    # 2. If email already exists in auth.users (possibly under another UUID), look it up
    try:
        link_resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/admin/generate_link",
            headers=auth_headers,
            json={"type": "magiclink", "email": email},
        )
        if link_resp.status_code in (200, 201):
            link_data = link_resp.json()
            resolved = link_data.get("id") or (link_data.get("user") or {}).get("id")
            if resolved:
                return str(resolved)
    except Exception as exc:
        logger.debug(f"_ensure_auth_user generate_link lookup error: {exc}")

    # 3. Last resort: filtered list on /auth/v1/admin/users
    try:
        list_resp = await client.get(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            headers=auth_headers,
            params={"page": 1, "per_page": 20, "filter": email},
        )
        if list_resp.status_code == 200:
            users_list = list_resp.json().get("users") or []
            for u in users_list:
                if (u.get("email") or "").strip().lower() == email:
                    return str(u["id"])
    except Exception as exc:
        logger.debug(f"_ensure_auth_user list lookup error: {exc}")

    return target_id


async def ensure_user_exists(
    user_id: str,
    email: Optional[str] = None,
    full_name: Optional[str] = None,
) -> dict:
    """
    Ensure a user row exists in Supabase users_table.
    Uses the service-role key to bypass RLS on both reads and writes.
    Resolves by local SQLite DB, by user_id first, then by email (handles ID mismatch between auth providers).
    Auto-provisions auth.users + users_table record if not found.
    """
    # 0. If email is not passed or empty, resolve email from local SQLite DB using user_id
    resolved_email = (email or "").strip().lower()
    if not resolved_email:
        try:
            from app.db.session import SessionLocal
            from app.db.models import User
            with SessionLocal() as db:
                local_user = db.query(User).filter(User.id == user_id).first()
                if local_user and local_user.email:
                    resolved_email = local_user.email.strip().lower()
        except Exception as e:
            logger.debug(f"Could not resolve email from local SQLite: {e}")

    # Always use service-role key so RLS never blocks user provisioning
    admin_headers = {
        **SERVICE_HEADERS,
        "Prefer": "resolution=merge-duplicates,return=representation",
    }

    async with httpx.AsyncClient(timeout=12.0) as client:
        # 1. Lookup by ID directly
        resp = await client.get(
            _url("users_table"),
            params={"id": f"eq.{user_id}", "limit": "1"},
            headers=SERVICE_HEADERS,
        )
        if resp.status_code == 200:
            rows = resp.json()
            if rows:
                return rows[0]

        # 1b. If user_id is a 32-char hex (or non-standard UUID), lookup by formatted hyphenated UUID
        formatted_uuid = _normalize_uuid_str(user_id)
        if formatted_uuid != user_id:
            resp_uuid = await client.get(
                _url("users_table"),
                params={"id": f"eq.{formatted_uuid}", "limit": "1"},
                headers=SERVICE_HEADERS,
            )
            if resp_uuid.status_code == 200:
                rows_uuid = resp_uuid.json()
                if rows_uuid:
                    return rows_uuid[0]

        # 2. Lookup by email fallback (handles ID differences across SQLite/auth migrations)
        if resolved_email:
            resp_email = await client.get(
                _url("users_table"),
                params={"email": f"eq.{resolved_email}", "limit": "1"},
                headers=SERVICE_HEADERS,
            )
            if resp_email.status_code == 200:
                rows_email = resp_email.json()
                if rows_email:
                    return rows_email[0]

        # 3. Ensure backing row exists in auth.users (satisfies users_table_id_fkey), then insert users_table
        clean_email = (resolved_email or f"user_{formatted_uuid[:8]}@node.local").strip().lower()
        clean_name = (full_name or clean_email.split("@")[0]).strip() or "Merchant"
        target_id = await _ensure_auth_user(client, formatted_uuid, clean_email, clean_name)

        # Check again by target_id in case auth.users lookup resolved an existing Supabase user
        if target_id != formatted_uuid:
            check_existing = await client.get(
                _url("users_table"),
                params={"id": f"eq.{target_id}", "limit": "1"},
                headers=SERVICE_HEADERS,
            )
            if check_existing.status_code == 200 and check_existing.json():
                return check_existing.json()[0]

        payload = {
            "id": target_id,
            "email": clean_email,
            "full_name": clean_name,
            "role": "customer",
        }
        create_resp = await client.post(
            f"{_url('users_table')}?on_conflict=id",
            json=payload,
            headers=admin_headers,
        )
        if create_resp.status_code in (200, 201):
            rows_created = create_resp.json()
            if rows_created:
                logger.info(f"Auto-provisioned users_table row for user_id={target_id}")
                return rows_created[0]
        elif create_resp.status_code == 409 or "duplicate" in create_resp.text.lower() or "unique" in create_resp.text.lower():
            # Duplicate email — fetch existing row by email
            resp_email2 = await client.get(
                _url("users_table"),
                params={"email": f"eq.{clean_email}", "limit": "1"},
                headers=SERVICE_HEADERS,
            )
            if resp_email2.status_code == 200:
                rows_email2 = resp_email2.json()
                if rows_email2:
                    return rows_email2[0]

        logger.warning(
            f"ensure_user_exists: failed to create user row "
            f"({create_resp.status_code}): {create_resp.text[:200]}"
        )
        return payload


async def fetch_user(user_id: str, email: Optional[str] = None) -> Optional[dict]:
    """Fetch user row by id or email with auto-provisioning."""
    try:
        return await ensure_user_exists(user_id, email=email)
    except Exception as exc:
        logger.warning(f"Error fetching/ensuring user in Supabase: {exc}")
        return {
            "id": user_id,
            "email": email or "",
            "full_name": "",
            "role": "customer",
        }


async def upsert_user(user_id: str, data: dict, email: Optional[str] = None) -> dict:
    """Upsert user fields (full_name, profile_pic_url, etc.)."""
    user = await ensure_user_exists(user_id, email=email)
    resolved_id = user["id"]
    payload = {**data, "id": resolved_id}

    upsert_headers = {
        **HEADERS,
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{_url('users_table')}?on_conflict=id",
            json=payload,
            headers=upsert_headers,
        )
        resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else payload


async def update_user_role(user_id: str, new_role: str, email: Optional[str] = None) -> dict:
    """Update role in users_table (e.g. promote to 'supplier')."""
    user = await ensure_user_exists(user_id, email=email)
    resolved_id = user["id"]

    patch_headers = {
        **HEADERS,
        "Prefer": "return=representation",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.patch(
            _url("users_table"),
            params={"id": f"eq.{resolved_id}"},
            json={"role": new_role},
            headers=patch_headers,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else {**user, "role": new_role}


# ---- business_table helpers ----


async def fetch_business(user_id: str, email: Optional[str] = None) -> Optional[dict]:
    """Fetch business profile for a user using resolved Supabase user_id."""
    user = await ensure_user_exists(user_id, email=email)
    resolved_id = user["id"]

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            _url("business_table"),
            params={"user_id": f"eq.{resolved_id}", "limit": "1"},
            headers=HEADERS,
        )
        if resp.status_code == 200:
            rows = resp.json()
            return rows[0] if rows else None
        return None


async def upsert_business(user_id: str, data: dict, email: Optional[str] = None) -> dict:
    """
    Upsert business profile for a user.
    1. Ensures the users_table row exists (creates it if missing, using service key).
    2. Sanitizes data to only valid business_table columns.
    3. Tries upsert using service-role key (bypasses RLS).
    4. Falls back to PATCH (update-only) if insert fails due to FK — meaning the
       business row already exists and only an update is needed.
    """
    user = await ensure_user_exists(user_id, email=email)
    resolved_id = user["id"]

    # Filter to only existing business_table columns to prevent 400 Bad Request
    clean_data = {k: v for k, v in data.items() if k in VALID_BUSINESS_COLUMNS}
    payload = {**clean_data, "user_id": resolved_id}

    admin_upsert_headers = {
        **SERVICE_HEADERS,
        "Prefer": "resolution=merge-duplicates,return=representation",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Attempt 1: Full upsert with service-role key (handles both INSERT and UPDATE)
        resp = await client.post(
            f"{_url('business_table')}?on_conflict=user_id",
            json=payload,
            headers=admin_upsert_headers,
        )

        if not resp.is_error:
            rows = resp.json()
            return rows[0] if rows else payload

        # Attempt 2: If upsert failed (e.g. FK violation or anon key fallback),
        # try a PATCH to update existing row — this never triggers an FK insert
        logger.warning(
            f"upsert_business: upsert failed ({resp.status_code}), "
            f"trying PATCH fallback: {resp.text[:200]}"
        )
        patch_headers = {
            **SERVICE_HEADERS,
            "Prefer": "return=representation",
        }
        patch_resp = await client.patch(
            _url("business_table"),
            params={"user_id": f"eq.{resolved_id}"},
            json=clean_data,
            headers=patch_headers,
        )
        if patch_resp.status_code in (200, 204):
            rows = patch_resp.json() if patch_resp.content else []
            if rows:
                return rows[0]
            # Row existed and was updated but nothing returned — fetch it
            fetch_resp = await client.get(
                _url("business_table"),
                params={"user_id": f"eq.{resolved_id}", "limit": "1"},
                headers=SERVICE_HEADERS,
            )
            if fetch_resp.status_code == 200:
                fetch_rows = fetch_resp.json()
                return fetch_rows[0] if fetch_rows else payload

        # Both attempts failed — raise the original error
        logger.error(f"Supabase upsert_business total failure: {resp.text}")
        resp.raise_for_status()
        return payload


# ---- trading terms (JSONB column on users_table) ----


async def update_trading_terms(user_id: str, terms: Any, email: Optional[str] = None) -> dict:
    """Store trading terms as JSONB in users_table.trading_terms column."""
    user = await ensure_user_exists(user_id, email=email)
    resolved_id = user["id"]
    trading_json = terms if isinstance(terms, str) else json.dumps(terms)
    patch_headers = {
        **HEADERS,
        "Prefer": "return=representation",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.patch(
            _url("users_table"),
            params={"id": f"eq.{resolved_id}"},
            json={"trading_terms": json.loads(trading_json)},
            headers=patch_headers,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else {}
