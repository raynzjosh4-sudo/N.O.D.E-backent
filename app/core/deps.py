import httpx
from typing import Any
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.security import decode_access_token
from app.core.config import supabase_url, supabase_anon_key

security = HTTPBearer()

SUPABASE_URL = supabase_url()
SUPABASE_KEY = supabase_anon_key()

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """Extract and validate the user ID from the Bearer JWT token."""
    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication credentials",
        )
    return user_id


async def get_current_user(
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Fetch user profile and role from users_table with resilient error handling."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/users_table",
                params={"id": f"eq.{user_id}", "limit": "1"},
                headers=HEADERS,
            )
            if resp.status_code == 200:
                rows = resp.json()
                if rows:
                    return rows[0]
                # Default role for existing auth user without profile row yet
                return {"id": user_id, "role": "customer"}
            elif resp.status_code == 404:
                return {"id": user_id, "role": "customer"}
            else:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to retrieve user privileges from authentication service.",
                )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication database is temporarily unreachable.",
        ) from exc


async def require_supplier_or_admin(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Ensure the user is a registered supplier or an administrator."""
    role = (user.get("role") or "").lower()
    if role not in ("supplier", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted: You must be a confirmed supplier or administrator to perform this action.",
        )
    return user


async def require_admin(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Ensure the user has an admin role."""
    role = (user.get("role") or "").lower()
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted: Administrator privileges required.",
        )
    return user
