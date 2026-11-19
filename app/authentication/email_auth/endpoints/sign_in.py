from datetime import datetime, timezone
import logging
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.authentication.email_auth.models.email_password import EmailPasswordRequest
from app.authentication.email_auth.send_auth_email import send_email
from app.core.config import supabase_anon_key, supabase_url
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.db.session import get_db
from app.profile import supabase_client as sb

logger = logging.getLogger(__name__)

router = APIRouter()

SUPABASE_URL = supabase_url()
SUPABASE_KEY = supabase_anon_key()


def parse_device_summary(user_agent: str | None) -> str:
    if not user_agent:
        return "Web / Mobile Client"
    ua = user_agent.lower()
    os_name = "Device"
    if "iphone" in ua:
        os_name = "iPhone"
    elif "ipad" in ua:
        os_name = "iPad"
    elif "android" in ua:
        os_name = "Android"
    elif "windows" in ua:
        os_name = "Windows PC"
    elif "macintosh" in ua or "mac os" in ua:
        os_name = "Mac"
    elif "linux" in ua:
        os_name = "Linux"

    browser_name = ""
    if "edg" in ua:
        browser_name = "Edge"
    elif "chrome" in ua and "chromium" not in ua:
        browser_name = "Chrome"
    elif "safari" in ua and "chrome" not in ua:
        browser_name = "Safari"
    elif "firefox" in ua:
        browser_name = "Firefox"
    elif "expo" in ua or "okhttp" in ua or "node" in ua:
        browser_name = "N.O.D.E. App"

    if browser_name:
        return f"{browser_name} on {os_name}"
    return os_name


async def send_security_sign_in_alert(
    recipient: str,
    sign_in_time: str,
    device_info: str,
    ip_address: str,
) -> None:
    try:
        await send_email(
            to=recipient,
            template="security_alert.html",
            code="https://node.app/auth/forgot-password",
            subject="Security Alert: New Sign-In to your N.O.D.E. Account",
            SIGN_IN_TIME=sign_in_time,
            DEVICE_INFO=device_info,
            IP_ADDRESS=ip_address,
        )
        logger.info(f"Security sign-in alert email sent successfully to {recipient}")
    except Exception as exc:
        logger.warning(f"Could not send security sign-in alert to {recipient}: {exc}")


@router.post("/email/sign-in")
async def sign_in(
    payload: EmailPasswordRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    email = str(payload.email).strip().lower()

    user = db.query(User).filter(User.email == email).first()
    is_valid_pwd = False

    if user and user.hashed_password:
        try:
            is_valid_pwd = verify_password(payload.password, user.hashed_password)
        except Exception:
            is_valid_pwd = False

    # Resilient fallback: verify against Supabase Auth in case account was provisioned in Supabase
    if not is_valid_pwd:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                sb_resp = await client.post(
                    f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
                    headers={
                        "apikey": SUPABASE_KEY,
                        "Content-Type": "application/json",
                    },
                    json={"email": email, "password": payload.password},
                )
                if sb_resp.status_code == 200:
                    sb_data = sb_resp.json()
                    sb_user_data = sb_data.get("user") or {}
                    is_valid_pwd = True

                    # Synchronize into local SQLite User table
                    if not user:
                        user = User(
                            id=sb_user_data.get("id") or uuid4().hex,
                            email=email,
                            hashed_password=hash_password(payload.password),
                            is_verified=True,
                            is_active=True,
                        )
                        db.add(user)
                    else:
                        user.hashed_password = hash_password(payload.password)
                        user.is_verified = True
                        user.is_active = True
                    db.commit()
                    db.refresh(user)
        except Exception as e:
            logger.warning(f"Supabase auth fallback check: {e}")

    if not is_valid_pwd or not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email address has not been verified. Please verify your email first.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated.",
        )

    # Fetch / provision unified Supabase user row for role and Supabase ID
    sb_user = await sb.ensure_user_exists(user.id, email=user.email)
    effective_id = sb_user.get("id") or user.id
    effective_role = sb_user.get("role") or "customer"
    effective_name = sb_user.get("full_name") or user.email.split("@")[0]
    effective_pic = sb_user.get("profile_pic_url")

    access_token = create_access_token(
        data={"sub": effective_id, "email": user.email, "role": effective_role}
    )

    user_dict = user.to_dict()
    user_dict["id"] = effective_id
    user_dict["role"] = effective_role
    user_dict["full_name"] = effective_name
    user_dict["profile_pic_url"] = effective_pic

    # ── Dispatch Security Sign-In Alert Email in the Background ──
    try:
        now_str = datetime.now(timezone.utc).strftime("%B %d, %Y at %I:%M %p UTC")
        raw_ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else "Unknown IP")
        client_ip = raw_ip.split(",")[0].strip() if "," in raw_ip else raw_ip
        device_summary = parse_device_summary(request.headers.get("user-agent"))

        background_tasks.add_task(
            send_security_sign_in_alert,
            recipient=user.email,
            sign_in_time=now_str,
            device_info=device_summary,
            ip_address=client_ip,
        )
    except Exception as e:
        logger.warning(f"Failed to schedule security sign-in alert email: {e}")

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "access_token": access_token,
            "token_type": "bearer",
            "user": user_dict,
        },
    )
