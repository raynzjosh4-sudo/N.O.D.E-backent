from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.security import decode_access_token

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return user_id


async def get_optional_user_id(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
) -> Optional[str]:
    if not credentials:
        return None
    try:
        payload = decode_access_token(credentials.credentials)
        return payload.get("sub")
    except Exception:
        return None
