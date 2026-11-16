"""
Profile & Business API router.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from app.profile.deps import get_current_user_id
from app.profile.schemas import (
    BusinessProfile,
    ProfileResponse,
    TradingTermsRequest,
    TradingTermsResponse,
    UpdateBusinessRequest,
    UpdateUserRequest,
    UserProfile,
)
from app.profile import supabase_client as sb

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])


# ---------- GET /api/v1/profile ----------


@router.get("", response_model=ProfileResponse)
async def get_my_profile(
    user_id: str = Depends(get_current_user_id),
):
    """Return the authenticated user's profile + business info."""
    user_row = await sb.fetch_user(user_id)
    if not user_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user = UserProfile(
        id=user_row["id"],
        full_name=user_row.get("full_name", ""),
        email=user_row.get("email", ""),
        role=user_row.get("role", "buyer"),
        profile_pic_url=user_row.get("profile_pic_url"),
    )

    biz_row = await sb.fetch_business(user_id)
    business = (
        BusinessProfile(
            user_id=biz_row["user_id"],
            legal_name=biz_row.get("legal_name", ""),
            phone_number=biz_row.get("phone_number"),
            latitude=biz_row.get("latitude"),
            longitude=biz_row.get("longitude"),
            region=biz_row.get("region"),
            city=biz_row.get("city"),
            physical_address=biz_row.get("physical_address"),
        )
        if biz_row
        else None
    )

    return ProfileResponse(user=user, business=business)


# ---------- PUT /api/v1/profile ----------


@router.put("", response_model=UserProfile)
async def update_user_profile(
    body: UpdateUserRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Update the authenticated user's basic info."""
    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields to update",
        )

    row = await sb.upsert_user(user_id, update_data)
    return UserProfile(
        id=row["id"],
        full_name=row.get("full_name", ""),
        email=row.get("email", ""),
        role=row.get("role", "buyer"),
        profile_pic_url=row.get("profile_pic_url"),
    )


# ---------- PUT /api/v1/profile/business ----------


@router.put("/business", response_model=BusinessProfile)
async def update_business_profile(
    body: UpdateBusinessRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Upsert the authenticated user's business profile."""
    row = await sb.upsert_business(user_id, body.model_dump(exclude_none=True))
    return BusinessProfile(
        user_id=row.get("user_id", user_id),
        legal_name=row.get("legal_name", body.legal_name),
        phone_number=row.get("phone_number"),
        latitude=row.get("latitude"),
        longitude=row.get("longitude"),
        region=row.get("region"),
        city=row.get("city"),
        physical_address=row.get("physical_address"),
    )


# ---------- PUT /api/v1/profile/trading-terms ----------


@router.put("/trading-terms", response_model=TradingTermsResponse)
async def update_trading_terms(
    body: TradingTermsRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Store trading terms (JSONB) on the user row."""
    terms_dict = body.model_dump(exclude_none=True)
    row = await sb.update_trading_terms(user_id, terms_dict)
    return TradingTermsResponse(trading_terms=row.get("trading_terms", terms_dict))
