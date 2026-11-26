"""
Supabase REST client for orders and drafts.
Operates on `supplier_orders` and `draft_orders` tables via httpx.
"""
from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional
import uuid

import httpx

from app.core.config import supabase_url, supabase_admin_config, supabase_anon_key
from app.profile.supabase_client import ensure_user_exists

logger = logging.getLogger(__name__)

SUPABASE_URL = supabase_url()
_SUPABASE_URL, SUPABASE_KEY = supabase_admin_config()

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

SERVICE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

TABLE_ORDERS = "supplier_orders"
TABLE_DRAFTS = "draft_orders"


def _url(table: str = TABLE_ORDERS) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"


def _to_uuid(id_val: Any) -> str:
    """Normalize or generate a deterministic UUID string."""
    s = str(id_val or "").strip()
    try:
        return str(uuid.UUID(s))
    except (ValueError, AttributeError):
        if not s:
            return str(uuid.uuid4())
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, s))


# ── Create / Upsert Wholesale Orders ─────────────────────────────

async def upsert_order(data: dict[str, Any]) -> dict:
    """
    Upsert an order row into supplier_orders on `order_id`.
    Resolves user_id to ensure foreign key passes.
    """
    user_id = data.get("user_id")
    resolved_user_id = None
    if user_id:
        try:
            user_row = await ensure_user_exists(user_id)
            if user_row and user_row.get("id"):
                resolved_user_id = user_row["id"]
        except Exception as e:
            logger.warning(f"Could not resolve user_id for order: {e}")

    # Fallback to any valid users_table row if foreign key would otherwise violate
    if not resolved_user_id:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(_url("users_table"), params={"select": "id", "limit": "1"}, headers=SERVICE_HEADERS)
                if r.status_code == 200 and r.json():
                    resolved_user_id = r.json()[0]["id"]
        except Exception:
            pass

    data["user_id"] = resolved_user_id or _to_uuid(user_id)

    # Normalize supplier_id to valid UUID or None
    supplier_id = data.get("supplier_id")
    if supplier_id and supplier_id != "supplier-main":
        try:
            data["supplier_id"] = _to_uuid(supplier_id)
        except Exception:
            data["supplier_id"] = None
    else:
        data["supplier_id"] = None

    # Clean optional UUID fields
    if not data.get("pdf_id"):
        data["pdf_id"] = None
    else:
        try:
            data["pdf_id"] = _to_uuid(data["pdf_id"])
        except Exception:
            data["pdf_id"] = None

    headers = {
        **SERVICE_HEADERS,
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            _url(TABLE_ORDERS),
            json=data,
            headers=headers,
            params={"on_conflict": "order_id"},
        )
        if resp.is_error:
            logger.error(f"supplier_orders upsert failed: {resp.status_code} {resp.text}")
            resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else data


# ── Read Wholesale Orders ────────────────────────────────────────

async def fetch_orders_for_user(
    user_id: str,
    *,
    page: int = 1,
    limit: int = 20,
) -> dict:
    """
    Paginated orders where user_id = current_user, ordered by created_at desc.
    """
    resolved_id = user_id
    try:
        user_row = await ensure_user_exists(user_id)
        resolved_id = user_row["id"]
    except Exception:
        pass

    offset = (page - 1) * limit
    headers = {**SERVICE_HEADERS, "Prefer": "count=exact"}
    params: dict[str, Any] = {
        "or": f"(user_id.eq.{resolved_id},user_id.eq.{user_id})",
        "order": "created_at.desc",
        "limit": limit,
        "offset": offset,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(_url(TABLE_ORDERS), params=params, headers=headers)
        resp.raise_for_status()
    rows = resp.json()
    total = int(resp.headers.get("content-range", "0/0").split("/")[-1] or 0)
    return {"orders": rows, "total": total}


async def fetch_order_by_id(order_id: str, user_id: str) -> Optional[dict]:
    """
    Fetch a single order where order_id = param AND (user_id = me OR supplier_id = me).
    """
    resolved_id = user_id
    try:
        user_row = await ensure_user_exists(user_id)
        resolved_id = user_row["id"]
    except Exception:
        pass

    params = {
        "order_id": f"eq.{order_id}",
        "or": f"(user_id.eq.{resolved_id},user_id.eq.{user_id},supplier_id.eq.{user_id},supplier_id.eq.{resolved_id})",
        "limit": 1,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(_url(TABLE_ORDERS), params=params, headers=SERVICE_HEADERS)
        resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


# ── Supplier Incoming Orders ────────────────────────────────────

async def fetch_supplier_incoming(
    supplier_id: str,
    *,
    page: int = 1,
    limit: int = 20,
    status_filter: Optional[str] = None,
) -> dict:
    offset = (page - 1) * limit
    headers = {**SERVICE_HEADERS, "Prefer": "count=exact"}
    params: dict[str, Any] = {
        "supplier_id": f"eq.{supplier_id}",
        "order": "created_at.desc",
        "limit": limit,
        "offset": offset,
    }
    if status_filter:
        params["status"] = f"eq.{status_filter}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(_url(TABLE_ORDERS), params=params, headers=headers)
        resp.raise_for_status()
    rows = resp.json()
    total = int(resp.headers.get("content-range", "0/0").split("/")[-1] or 0)
    return {"orders": rows, "total": total}


# ── Update Order Status ─────────────────────────────────────────

async def _deduct_stock_for_completed_order(order_row: dict) -> None:
    """
    When an order transitions to 'completed', deduct fulfilled units from
    products_table.current_stock in Supabase for all products in the order.
    """
    entries = order_row.get("entries_json") or order_row.get("entries") or []
    if isinstance(entries, str):
        try:
            entries = json.loads(entries)
        except Exception:
            entries = []

    if not isinstance(entries, list):
        return

    # Calculate total units per product_id
    deductions: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pid = (
            entry.get("product_id")
            or entry.get("productId")
            or (entry.get("product") or {}).get("id")
            or (entry.get("product_snapshot") or {}).get("id")
        )
        if not pid:
            continue
        pid_str = str(pid).strip()

        # Extract units
        units = int(entry.get("total_units") or entry.get("totalUnits") or 0)
        if units <= 0:
            groups = entry.get("groups") or []
            for g in groups:
                if isinstance(g, dict):
                    sq = g.get("sizeQtys") or g.get("size_quantities") or {}
                    if isinstance(sq, dict):
                        units += sum(int(v or 0) for v in sq.values())
        if units <= 0:
            units = int(entry.get("quantity") or entry.get("units") or 1)

        deductions[pid_str] = deductions.get(pid_str, 0) + max(1, units)

    if not deductions:
        return

    # Deduct stock for each product in Supabase
    async with httpx.AsyncClient(timeout=15.0) as client:
        for pid_str, qty_to_deduct in deductions.items():
            try:
                # 1. Fetch current stock
                resp = await client.get(
                    _url("products_table"),
                    params={"id": f"eq.{pid_str}", "select": "id,current_stock"},
                    headers=SERVICE_HEADERS,
                )
                if resp.status_code == 200 and resp.json():
                    row = resp.json()[0]
                    curr_stock = int(row.get("current_stock") or 0)
                    new_stock = max(0, curr_stock - qty_to_deduct)
                    # 2. Patch new stock
                    patch_resp = await client.patch(
                        _url("products_table"),
                        params={"id": f"eq.{pid_str}"},
                        json={"current_stock": new_stock},
                        headers=SERVICE_HEADERS,
                    )
                    if patch_resp.is_error:
                        logger.warning(f"Failed to update stock for product {pid_str}: {patch_resp.status_code} {patch_resp.text}")
                    else:
                        logger.info(f"Updated product {pid_str} stock: {curr_stock} -> {new_stock} (-{qty_to_deduct})")
            except Exception as ex:
                logger.warning(f"Stock deduction error for product {pid_str}: {ex}")


async def update_order_status(
    order_id: str,
    user_id: str,
    new_status: str,
) -> Optional[dict]:
    resolved_id = user_id
    try:
        user_row = await ensure_user_exists(user_id)
        resolved_id = user_row["id"]
    except Exception:
        pass

    headers = {**SERVICE_HEADERS, "Prefer": "return=representation"}
    params = {
        "order_id": f"eq.{order_id}",
        "or": f"(user_id.eq.{resolved_id},user_id.eq.{user_id},supplier_id.eq.{user_id},supplier_id.eq.{resolved_id})",
    }
    body = {
        "status": new_status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.patch(_url(TABLE_ORDERS), json=body, params=params, headers=headers)
        resp.raise_for_status()
    rows = resp.json()
    updated_order = rows[0] if rows else None

    # If status is now completed, automatically deduct in-stock units
    if updated_order and (new_status or "").lower() == "completed":
        try:
            await _deduct_stock_for_completed_order(updated_order)
        except Exception as e:
            logger.error(f"Error deducting stock for order {order_id}: {e}")

    return updated_order


# ── Delete Wholesale Order ──────────────────────────────────────

async def delete_order(order_id: str, user_id: str) -> bool:
    resolved_id = user_id
    try:
        user_row = await ensure_user_exists(user_id)
        resolved_id = user_row["id"]
    except Exception:
        pass

    headers = {**SERVICE_HEADERS, "Prefer": "return=representation"}
    params = {
        "order_id": f"eq.{order_id}",
        "or": f"(user_id.eq.{resolved_id},user_id.eq.{user_id})",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(_url(TABLE_ORDERS), params=params, headers=headers)
        resp.raise_for_status()
    rows = resp.json()
    return len(rows) > 0


# ── Draft Orders (Stored in draft_orders table) ──────────────────

async def upsert_draft_order(data: dict[str, Any], user_id: str) -> dict:
    """
    Save or upsert a draft order.
    Targets `draft_orders` table (id, user_id, entries_json, status, last_modified).
    """
    resolved_user_id = None
    if user_id:
        try:
            user_row = await ensure_user_exists(user_id)
            if user_row and user_row.get("id"):
                resolved_user_id = user_row["id"]
        except Exception as e:
            logger.warning(f"Could not ensure user exists for draft: {e}")

    # Fallback to any valid users_table row if foreign key would otherwise violate
    if not resolved_user_id:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(_url("users_table"), params={"select": "id", "limit": "1"}, headers=SERVICE_HEADERS)
                if r.status_code == 200 and r.json():
                    resolved_user_id = r.json()[0]["id"]
        except Exception:
            pass

    resolved_user_id = resolved_user_id or _to_uuid(user_id)
    draft_id = _to_uuid(data.get("id") or data.get("order_id"))
    now = datetime.now(timezone.utc).isoformat()

    # Preserve rich metadata inside entries_json so title, supplier, and amounts persist
    raw_entries = data.get("entries_json") or []
    if not isinstance(raw_entries, list):
        raw_entries = [raw_entries]

    # Stamp metadata on entries for frontend & Dart compatibility
    metadata = {
        "order_id": data.get("order_id") or draft_id,
        "supplier_id": data.get("supplier_id") or "",
        "business_name": data.get("business_name") or "Wholesale Buyer",
        "business_phone": data.get("business_phone") or "",
        "business_city": data.get("business_city") or "Kampala",
        "business_address": data.get("business_address") or "",
        "total_amount": float(data.get("total_amount") or 0.0),
    }

    stamped_entries = []
    for entry in raw_entries:
        if isinstance(entry, dict):
            entry_copy = dict(entry)
            for k, v in metadata.items():
                if k not in entry_copy:
                    entry_copy[k] = v
            stamped_entries.append(entry_copy)
        else:
            stamped_entries.append(entry)

    if not stamped_entries:
        stamped_entries = [metadata]

    draft_row = {
        "id": draft_id,
        "user_id": resolved_user_id,
        "entries_json": stamped_entries,
        "status": "DRAFT",
        "created_at": data.get("created_at") or now,
        "last_modified": now,
    }

    headers = {
        **SERVICE_HEADERS,
        "Prefer": "resolution=merge-duplicates,return=representation",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            _url(TABLE_DRAFTS),
            json=draft_row,
            headers=headers,
            params={"on_conflict": "id"},
        )
        if resp.is_error:
            logger.error(f"draft_orders upsert failed: {resp.status_code} {resp.text}")
            resp.raise_for_status()

    # Construct complete response matching OrderResponse
    return {
        "id": draft_id,
        "order_id": metadata["order_id"],
        "user_id": resolved_user_id,
        "supplier_id": metadata["supplier_id"],
        "business_name": metadata["business_name"],
        "business_phone": metadata["business_phone"],
        "business_city": metadata["business_city"],
        "business_address": metadata["business_address"],
        "total_amount": metadata["total_amount"],
        "entries_json": stamped_entries,
        "status": "draft",
        "created_at": draft_row["created_at"],
        "updated_at": draft_row["last_modified"],
    }


async def fetch_drafts_for_user(user_id: str) -> list[dict]:
    """
    Fetch all draft orders for a user from `draft_orders` table.
    """
    resolved_id = user_id
    try:
        user_row = await ensure_user_exists(user_id)
        resolved_id = user_row["id"]
    except Exception:
        pass

    params = {
        "or": f"(user_id.eq.{resolved_id},user_id.eq.{user_id})",
        "order": "last_modified.desc",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(_url(TABLE_DRAFTS), params=params, headers=SERVICE_HEADERS)
        if resp.is_error:
            logger.warning(f"Error fetching drafts: {resp.status_code} {resp.text}")
            return []
        rows = resp.json()

    results = []
    for r in rows:
        entries = r.get("entries_json") or []
        first_entry = entries[0] if (isinstance(entries, list) and len(entries) > 0 and isinstance(entries[0], dict)) else {}
        results.append({
            "id": r.get("id"),
            "order_id": first_entry.get("order_id") or r.get("id"),
            "user_id": r.get("user_id"),
            "supplier_id": first_entry.get("supplier_id") or "",
            "business_name": first_entry.get("business_name") or "Wholesale Order",
            "business_phone": first_entry.get("business_phone") or "",
            "business_city": first_entry.get("business_city") or "Kampala",
            "business_address": first_entry.get("business_address") or "",
            "total_amount": float(first_entry.get("total_amount") or 0.0),
            "entries_json": entries,
            "status": "draft",
            "created_at": r.get("created_at"),
            "updated_at": r.get("last_modified") or r.get("created_at"),
        })

    return results


async def delete_draft_for_user(draft_id: str, user_id: str) -> bool:
    """
    Delete draft from `draft_orders`.
    """
    resolved_id = user_id
    try:
        user_row = await ensure_user_exists(user_id)
        resolved_id = user_row["id"]
    except Exception:
        pass

    target_uuid = _to_uuid(draft_id)
    headers = {**SERVICE_HEADERS, "Prefer": "return=representation"}
    params = {
        "or": f"(user_id.eq.{resolved_id},user_id.eq.{user_id})",
        "id": f"eq.{target_uuid}",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(_url(TABLE_DRAFTS), params=params, headers=headers)
        if resp.is_error:
            logger.warning(f"delete_draft error: {resp.status_code} {resp.text}")
            return False
        rows = resp.json()
        return len(rows) > 0
