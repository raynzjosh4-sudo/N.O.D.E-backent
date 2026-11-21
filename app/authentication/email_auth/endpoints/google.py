import logging
from typing import Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.db.models import User
from app.db.session import get_db
from app.profile import supabase_client as sb

logger = logging.getLogger(__name__)

router = APIRouter()


class GoogleAuthRequest(BaseModel):
    id_token: Optional[str] = None
    email: Optional[EmailStr] = None
    name: Optional[str] = None
    avatar_url: Optional[str] = None


@router.post("/google")
async def google_auth(
    payload: GoogleAuthRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """
    Authenticate a user via Google Sign-In or Google One Tap.
    Accepts a Google ID token or pre-verified profile payload, creates or updates
    the user account, syncs to the Supabase users_table, and returns an access token.
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
    user = db.query(User).filter(User.email == email).first()
    if not user:
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
