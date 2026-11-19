import httpx
import logging
from typing import Any, Optional
from app.core.config import supabase_url, supabase_anon_key, supabase_admin_config
from app.profile.supabase_client import ensure_user_exists

logger = logging.getLogger(__name__)

SUPABASE_URL = supabase_url()
SUPABASE_KEY = supabase_anon_key()

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

_svc_url, _svc_key = supabase_admin_config()
SERVICE_HEADERS = {
    "apikey": _svc_key,
    "Authorization": f"Bearer {_svc_key}",
    "Content-Type": "application/json",
}


def _url(table: str = "saved_items") -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"


async def get_user_saved_items(user_id: str, email: Optional[str] = None) -> list[dict[str, Any]]:
    user = await ensure_user_exists(user_id, email=email)
    resolved_id = user["id"]
    params = {
        "user_id": f"eq.{resolved_id}",
        "order": "created_at.desc",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(_url(), params=params, headers=SERVICE_HEADERS)
        if resp.is_error:
            logger.error(f"get_user_saved_items error {resp.status_code}: {resp.text}")
            return []
        return resp.json()


async def upsert_saved_item(
    user_id: str,
    product_id: str,
    quantity: int = 1,
    selected_color: Optional[str] = None,
    selected_size: Optional[str] = None,
    product_snapshot: Optional[dict[str, Any]] = None,
    email: Optional[str] = None,
) -> dict[str, Any]:
    user = await ensure_user_exists(user_id, email=email)
    resolved_id = user["id"]

    payload = {
        "user_id": resolved_id,
        "product_id": product_id,
        "quantity": quantity,
        "selected_color": selected_color,
        "selected_size": selected_size,
        "product_snapshot": product_snapshot or {},
    }
    headers = {
        **SERVICE_HEADERS,
        "Prefer": "return=representation,resolution=merge-duplicates",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(_url(), json=payload, headers=headers)
        if resp.is_error:
            logger.error(f"upsert_saved_item error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
        data = resp.json()
        return data[0] if data else payload


async def delete_saved_item(saved_item_id: str, user_id: str, email: Optional[str] = None) -> bool:
    user = await ensure_user_exists(user_id, email=email)
    resolved_id = user["id"]
    params = {
        "id": f"eq.{saved_item_id}",
        "user_id": f"eq.{resolved_id}",
    }
    headers = {**SERVICE_HEADERS, "Prefer": "return=representation"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(_url(), params=params, headers=headers)
        resp.raise_for_status()
        return len(resp.json()) > 0


async def check_item_saved(user_id: str, product_id: str, email: Optional[str] = None) -> tuple[bool, Optional[str]]:
    user = await ensure_user_exists(user_id, email=email)
    resolved_id = user["id"]
    params = {
        "user_id": f"eq.{resolved_id}",
        "product_id": f"eq.{product_id}",
        "select": "id",
        "limit": 1,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(_url(), params=params, headers=SERVICE_HEADERS)
        if resp.is_error:
            return False, None
        rows = resp.json()
        if rows:
            return True, rows[0]["id"]
        return False, None
