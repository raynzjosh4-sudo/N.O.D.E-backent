"""
Cloudflare R2 media upload service for N.O.D.E.
Handles uploading product images and assets to Cloudflare R2 / media CDN,
matching the Flutter CloudMediaService integration.
"""
import base64
import io
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple
import httpx
from fastapi import HTTPException, status

from app.core.config import (
    backend_base_url,
    cloudflare_media_endpoint,
    supabase_anon_key,
    supabase_admin_config,
    is_production,
)


def get_cloudflare_media_url() -> str:
    """Returns the Cloudflare R2 worker upload endpoint."""
    return cloudflare_media_endpoint()


def compress_image(
    file_bytes: bytes,
    max_dim: int = 1400,
    quality: int = 82,
    content_type: str = "image/jpeg",
) -> Tuple[bytes, str]:
    """
    Compress / resize an image using Pillow.
    - Applies EXIF auto-rotation so mobile photos appear upright.
    - Scales down images whose longest dimension exceeds max_dim (default 1400 px).
    - Saves as JPEG at the given quality (default 82).
    Returns: (compressed_bytes, new_content_type)
    """
    try:
        from PIL import Image, ImageOps  # type: ignore

        img = Image.open(io.BytesIO(file_bytes))

        # Auto-rotate based on EXIF orientation tag (mobile cameras)
        img = ImageOps.exif_transpose(img)

        # Downscale if necessary (keeps aspect ratio)
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)

        # Convert modes incompatible with JPEG to RGB
        if img.mode in ("RGBA", "P", "LA", "L"):
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception as exc:
        # If Pillow fails for any reason, return the original bytes untouched
        print(f"⚠️ [CloudMediaService] Image compression skipped: {exc}")
        return file_bytes, content_type


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
    Compresses the image, then uploads to Cloudflare R2 via its _node/media worker.
    Falls back to Supabase Storage and then local static storage on failure.
    Always returns an absolute HTTP URL.
    """
    # --- Compress before upload (skip SVG/GIF) ---
    if content_type not in ("image/svg+xml", "image/gif"):
        file_bytes, content_type = compress_image(file_bytes, content_type=content_type)
        # Update filename extension to .jpg after compression
        stem = Path(filename).stem
        filename = f"{stem}.jpg"

    endpoint = get_cloudflare_media_url()
    # Prefer the user's own session token; anon key causes 401 on the Cloudflare worker
    auth_token = token or supabase_anon_key()
    backend_secret = (
        os.getenv("BACKEND_SECRET")
        or os.getenv("CLOUDFLARE_WORKER_SECRET")
        or "node-r2-super-secret-2026"
    )

    headers = {
        "User-Agent": "NODE-Backend/1.0 (CloudflareMediaClient)",
        "Authorization": f"Bearer {auth_token}",
        "X-Backend-Secret": backend_secret,
        "X-User-Id": "1a47f0fe-3f4d-487f-95b5-1f7cfe27b7b7",
        "Content-Type": content_type or "application/octet-stream",
        "Content-Length": str(len(file_bytes)),
        "X-Node-Folder": folder,
    }

    cf_error_detail = None
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(endpoint, headers=headers, content=file_bytes)
            if resp.status_code in (200, 201):
                data = resp.json()
                if isinstance(data, dict) and data.get("url"):
                    return data["url"]
                cf_error_detail = f"Cloudflare upload returned no public URL: {resp.text}"
                print(f"[CloudMediaService] {cf_error_detail}")
            else:
                cf_error_detail = f"Cloudflare {endpoint} returned HTTP {resp.status_code}: {resp.text}"
                print(f"[CloudMediaService] {cf_error_detail}")
    except Exception as exc:
        cf_error_detail = f"Cloudflare R2 connection error: {exc}"
        print(f"⚠️ [CloudMediaService] {cf_error_detail}")

    # Fallback 1: Supabase Storage
    sb_url, sb_key = supabase_admin_config()
    if sb_url and sb_key and sb_key != supabase_anon_key():
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
                print(f"[CloudMediaService] Supabase Storage upload returned HTTP {sb_resp.status_code}.")
        except Exception as sb_exc:
            print(f"⚠️ [CloudMediaService] Supabase storage upload attempt failed: {sb_exc}")

    # Never fall back to Render local disk: persistent cloud storage is required.
    error_msg = f"Image upload to persistent cloud storage (Cloudflare R2) failed. {cf_error_detail or ''} Ephemeral storage is disabled; no persistent image was saved."
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=error_msg.strip(),
    )
