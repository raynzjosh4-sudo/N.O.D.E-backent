from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.authentication.email_auth.dependencies.bearer_token import bearer_token
from app.authentication.email_auth.models.password_update import PasswordUpdateRequest
from app.core.security import decode_access_token, hash_password
from app.db.models import User, VerificationToken
from app.db.session import get_db

router = APIRouter()


@router.put("/email/password")
async def update_password(
    payload: PasswordUpdateRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
    reset_token: Annotated[str | None, Query(alias="token")] = None,
) -> JSONResponse:
    user: User | None = None

    if authorization:
        # Authenticated user updating their own password
        raw_token = bearer_token(authorization)
        token_data = decode_access_token(raw_token)
        user_id = token_data.get("sub")
        user = db.query(User).filter(User.id == str(user_id)).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User account not found.",
            )

    elif reset_token:
        # User updating password via recovery reset token
        now = datetime.now(timezone.utc)
        token_record = (
            db.query(VerificationToken)
            .filter(
                VerificationToken.token == reset_token.strip(),
                VerificationToken.token_type == "recovery",
                VerificationToken.is_used == False,  # noqa: E712
            )
            .order_by(VerificationToken.id.desc())
            .first()
        )
        if not token_record:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired password reset token.",
            )

        expires_at = token_record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if now > expires_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password reset token has expired.",
            )

        user = db.query(User).filter(User.email == token_record.email).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User account not found.",
            )

        token_record.is_used = True

    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid Bearer token or reset token query parameter is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user.hashed_password = hash_password(payload.password)
    db.commit()

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Password has been successfully updated."},
    )
