"""
Cloudflare R2 media upload service for N.O.D.E.
Handles uploading product images and assets to Cloudflare R2 / media CDN,
matching the Flutter CloudMediaService integration.
"""
import base64
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple
import httpx
from fastapi import HTTPException, status

from app.core.config import (
    cloudflare_media_endpoint,
    r2_public_url,
    supabase_anon_key,
    supabase_url,
    is_production,
)


def get_cloudflare_media_url() -> str:
    """Returns the Cloudflare R2 worker upload endpoint."""
    return cloudflare_media_endpoint()


def decode_base64_image(data_str: str) -> Tuple[bytes, str, str]:
    """
    Decodes a base64 or Data-URI image string.
    Returns: (file_bytes, filename, content_type)
    """
    mime_type = "image/jpeg"
    ext = "jpg"

    # Match Data URI: data:image/png;base64,...
    match = re.match(r"^data:(image\/[a-zA-Z0-9\+\-]+);base64,(.*)$", data_str, re.DOTALL)
    if match:
        mime_type = match.group(1).lower()
        base64_str = match.group(2)
        if "png" in mime_type:
            ext = "png"
        elif "webp" in mime_type:
            ext = "webp"
        elif "gif" in mime_type:
            ext = "gif"
        elif "svg" in mime_type:
            ext = "svg+xml"
            ext = "svg"
    else:
        base64_str = data_str

    try:
        file_bytes = base64.b64decode(base64_str)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid base64 image data: {exc}",
        ) from exc

    filename = f"img_{int(time.time())}_{uuid.uuid4().hex[:8]}.{ext}"
    return file_bytes, filename, mime_type


async def upload_to_cloudflare(
    file_bytes: bytes,
    filename: str,
    content_type: str = "image/jpeg",
    folder: str = "products",
    token: Optional[str] = None,
) -> str:
    """
    Uploads raw image bytes to Cloudflare R2 via its _node/media worker endpoint.
    If Cloudflare fails (e.g. invalid external credentials in dev/offline), falls back
    to Supabase Storage or local static storage so catalog operations never fail.
    """
    endpoint = get_cloudflare_media_url()
    auth_token = token or supabase_anon_key()

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": content_type or "application/octet-stream",
        "X-Node-Folder": folder,
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(endpoint, headers=headers, content=file_bytes)
            if resp.status_code in (200, 201):
                data = resp.json()
                if isinstance(data, dict) and data.get("url"):
                    return data["url"]
    except Exception as exc:
        print(f"⚠️ [CloudMediaService] Cloudflare R2 upload attempt failed: {exc}")

    # Fallback 1: Supabase Storage
    sb_url = supabase_url()
    sb_key = supabase_anon_key()
    if sb_url and sb_key:
        safe_name = f"{folder}/{int(time.time())}_{uuid.uuid4().hex[:8]}_{Path(filename).name}"
        sb_storage_url = f"{sb_url}/storage/v1/object/products/{safe_name}"
        sb_headers = {
            "apikey": sb_key,
            "Authorization": f"Bearer {sb_key}",
            "Content-Type": content_type,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                sb_resp = await client.post(sb_storage_url, headers=sb_headers, content=file_bytes)
                if sb_resp.status_code in (200, 201):
                    return f"{sb_url}/storage/v1/object/public/products/{safe_name}"
        except Exception as sb_exc:
            print(f"⚠️ [CloudMediaService] Supabase storage upload attempt failed: {sb_exc}")

    # Fallback 2: Local static storage (for development/testing or when cloud is unreachable)
    static_upload_dir = Path(__file__).resolve().parent.parent / "static" / "uploads" / folder
    static_upload_dir.mkdir(parents=True, exist_ok=True)
    safe_local_name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{Path(filename).name}"
    local_file_path = static_upload_dir / safe_local_name
    local_file_path.write_bytes(file_bytes)

    # Return local accessible URL
    return f"/static/uploads/{folder}/{safe_local_name}"
