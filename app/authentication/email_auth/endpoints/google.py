from datetime import datetime, timezone
import logging
from typing import Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.authentication.email_auth.send_auth_email import send_auth_email
from app.core.security import create_access_token, hash_password
from app.db.models import User
from app.db.session import get_db
from app.profile import supabase_client as sb

logger = logging.getLogger(__name__)

router = APIRouter()


async def _send_google_auth_email(
    recipient: str,
    is_new_user: bool,
    client_ip: str,
    device_info: str,
):
    """
    Sends a Welcome email to brand new users, or a Security Sign-In Alert email
    to existing users when signing in with Google.
    """
    try:
        now_str = datetime.now(timezone.utc).strftime("%B %d, %Y at %I:%M %p UTC")
        req_id = uuid4().hex
        if is_new_user:
            await send_auth_email(
                recipient=recipient,
                subject="🎉 Welcome to N.O.D.E. Wholesale!",
                template_name="welcome.html",
                request_id=req_id,
                template_values={
                    "ACTION_LINK": "https://node.crimchart.com",
                },
            )
            logger.info(f"Dispatched Welcome email for new Google user {recipient}")
        else:
            clean_device = device_info[:80] if device_info else "Web Browser / Mobile Client"
            await send_auth_email(
                recipient=recipient,
                subject="🔒 Security Alert: New Sign-In to your N.O.D.E. Account",
                template_name="security_alert.html",
                request_id=req_id,
                template_values={
                    "SIGN_IN_TIME": now_str,
                    "DEVICE_INFO": clean_device,
                    "IP_ADDRESS": client_ip or "127.0.0.1",
                    "ACTION_LINK": "https://node.crimchart.com",
                },
            )
            logger.info(f"Dispatched Security Alert email for existing Google user {recipient}")
    except Exception as e:
        logger.warning(f"Failed to dispatch Google auth email to {recipient}: {e}")


class GoogleAuthRequest(BaseModel):
    id_token: Optional[str] = None
    email: Optional[EmailStr] = None
    name: Optional[str] = None
    avatar_url: Optional[str] = None


@router.post("/google")
async def google_auth(
    payload: GoogleAuthRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """
    Authenticate a user via Google Sign-In or Google One Tap.
    Accepts a Google ID token or pre-verified profile payload, creates or updates
    the user account, syncs to the Supabase users_table, and returns an access token.
    Sends a Welcome email if the user is new, or a Security alert if returning.
    """
    email = str(payload.email).strip().lower() if payload.email else None
    name = payload.name
    avatar_url = payload.avatar_url

    # If Google ID Token is provided, verify it with Google's tokeninfo endpoint
    if payload.id_token:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.get(
                    "https://oauth2.googleapis.com/tokeninfo",
                    params={"id_token": payload.id_token},
                )
                if res.status_code == 200:
                    data = res.json()
                    email = data.get("email", email)
                    name = data.get("name", name)
                    avatar_url = data.get("picture", avatar_url)
                else:
                    logger.warning(f"Google tokeninfo returned status {res.status_code}")
        except Exception as e:
            logger.warning(f"Failed to query Google tokeninfo: {e}")

    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google account email is required.",
        )

    # 1. Lookup or provision user in local SQLite DB
    is_new_user = False
    user = db.query(User).filter(User.email == email).first()
    if not user:
        is_new_user = True
        user = User(
            id=uuid4().hex,
            email=email,
            hashed_password=hash_password(uuid4().hex),
            is_active=True,
            is_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        user.is_verified = True
        user.is_active = True
        db.commit()
        db.refresh(user)

    # 2. Sync / ensure row exists in Supabase users_table
    try:
        sb_user = await sb.ensure_user_exists(
            user_id=user.id,
            email=user.email,
            full_name=name or user.email.split("@")[0],
        )
    except Exception as e:
        logger.warning(f"Supabase ensure_user_exists failed for Google user: {e}")
        sb_user = {}

    effective_id = sb_user.get("id") or user.id
    effective_role = sb_user.get("role") or "customer"
    effective_name = sb_user.get("full_name") or name or user.email.split("@")[0]
    effective_pic = sb_user.get("profile_pic_url") or avatar_url

    # 3. Issue JWT access token
    access_token = create_access_token(
        data={"sub": effective_id, "email": user.email, "role": effective_role}
    )

    # 4. Dispatch Welcome or Security Alert email in background
    client_ip = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("user-agent", "Web Browser")
    background_tasks.add_task(
        _send_google_auth_email,
        recipient=user.email,
        is_new_user=is_new_user,
        client_ip=client_ip,
        device_info=user_agent,
    )

    user_dict = {
        "id": effective_id,
        "email": user.email,
        "full_name": effective_name,
        "role": effective_role,
        "profile_pic_url": effective_pic,
        "is_verified": True,
        "is_active": True,
    }

    return JSONResponse(
        content={
            "access_token": access_token,
            "token_type": "bearer",
            "user": user_dict,
        }
    )
