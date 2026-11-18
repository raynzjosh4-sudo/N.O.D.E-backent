from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.authentication.email_auth.models.email_password import EmailPasswordRequest
from app.core.security import create_access_token, verify_password
from app.db.models import User
from app.db.session import get_db
from app.profile import supabase_client as sb

router = APIRouter()


@router.post("/email/sign-in")
async def sign_in(
    payload: EmailPasswordRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    email = str(payload.email).strip().lower()

    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
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

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "access_token": access_token,
            "token_type": "bearer",
            "user": user_dict,
        },
    )
