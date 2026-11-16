from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.authentication.email_auth.models.otp_verify import OtpVerifyRequest
from app.core.security import create_access_token
from app.db.models import User, VerificationToken
from app.db.session import get_db

router = APIRouter()


@router.post("/email/otp/verify")
async def verify_signup_otp(
    payload: OtpVerifyRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    email = str(payload.email).strip().lower()
    token = payload.token.strip()
    now = datetime.now(timezone.utc)

    # Find the most recent matching token
    token_record = (
        db.query(VerificationToken)
        .filter(
            VerificationToken.email == email,
            VerificationToken.token == token,
            VerificationToken.token_type == "signup",
            VerificationToken.is_used == False,  # noqa: E712
        )
        .order_by(VerificationToken.id.desc())
        .first()
    )

    if not token_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or already used verification code.",
        )

    expires_at = token_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if now > expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification code has expired. Please request a new code.",
        )

    # Mark token used
    token_record.is_used = True

    # Find and activate user
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found.",
        )

    user.is_verified = True
    user.is_active = True
    db.commit()
    db.refresh(user)

    # Generate JWT access token
    access_token = create_access_token(
        data={"sub": user.id, "email": user.email}
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "access_token": access_token,
            "token_type": "bearer",
            "user": user.to_dict(),
        },
    )
