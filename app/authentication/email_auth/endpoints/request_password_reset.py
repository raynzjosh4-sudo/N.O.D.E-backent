import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.authentication.email_auth.models.password_reset import PasswordResetRequest
from app.authentication.email_auth.send_auth_email import send_email
from app.db.models import User, VerificationToken
from app.db.session import get_db

router = APIRouter()


@router.post("/email/password-reset")
async def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    email = str(payload.email).strip().lower()

    user = db.query(User).filter(User.email == email).first()
    if user:
        # Invalidate old recovery tokens
        db.query(VerificationToken).filter(
            VerificationToken.email == email,
            VerificationToken.token_type == "recovery",
            VerificationToken.is_used == False,  # noqa: E712
        ).update({"is_used": True})

        # Generate recovery token
        reset_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        token_record = VerificationToken(
            email=email,
            token=reset_token,
            token_type="recovery",
            expires_at=expires_at,
            is_used=False,
        )
        db.add(token_record)
        db.commit()

        base_url = payload.redirect_to or "/reset-password"
        separator = "&" if "?" in base_url else "?"
        action_link = f"{base_url}{separator}token={reset_token}"

        # Send password reset email
        await send_email(email, "password_reset", action_link)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"message": "If an account exists for that address, a reset email has been sent."},
    )
