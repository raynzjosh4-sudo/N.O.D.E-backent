"""
Supabase REST client for profile & business data.
"""
import json
from typing import Any, Optional

import httpx

from app.core.config import supabase_url, supabase_anon_key

SUPABASE_URL = supabase_url()
SUPABASE_KEY = supabase_anon_key()

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def _url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"


# ---- users_table helpers ----


async def fetch_user(user_id: str) -> Optional[dict]:
    """Fetch a single user row by id."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _url("users_table"),
            params={"id": f"eq.{user_id}", "limit": "1"},
            headers=HEADERS,
        )
        resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def upsert_user(user_id: str, data: dict) -> dict:
    """Upsert user fields (PATCH-style via Supabase upsert)."""
    payload = {**data, "id": user_id}
    upsert_headers = {
        **HEADERS,
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _url("users_table"),
            json=payload,
            headers=upsert_headers,
        )
        resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else payload


# ---- business_table helpers ----


async def fetch_business(user_id: str) -> Optional[dict]:
    """Fetch business profile for a user."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _url("business_table"),
            params={"user_id": f"eq.{user_id}", "limit": "1"},
            headers=HEADERS,
        )
        resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def upsert_business(user_id: str, data: dict) -> dict:
    """Upsert business profile for a user."""
    payload = {**data, "user_id": user_id}
    upsert_headers = {
        **HEADERS,
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _url("business_table"),
            json=payload,
            headers=upsert_headers,
        )
        resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else payload


# ---- trading terms (JSONB column on users_table) ----


async def update_trading_terms(user_id: str, terms: Any) -> dict:
    """Store trading terms as JSONB in users_table.trading_terms column."""
    trading_json = terms if isinstance(terms, str) else json.dumps(terms)
    patch_headers = {
        **HEADERS,
        "Prefer": "return=representation",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            _url("users_table"),
            params={"id": f"eq.{user_id}"},
            json={"trading_terms": json.loads(trading_json)},
            headers=patch_headers,
        )
        resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else {}
