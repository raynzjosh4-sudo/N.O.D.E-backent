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

    biz_row = await sb.fetch_business(user_id)
    profile_pic = (
        user_row.get("profile_pic_url")
        or user_row.get("avatar_url")
        or (biz_row.get("logo_url") if biz_row else None)
    )

    user = UserProfile(
        id=user_row["id"],
        full_name=user_row.get("full_name", ""),
        email=user_row.get("email", ""),
        role=user_row.get("role", "buyer"),
        profile_pic_url=profile_pic,
    )

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
            logo_url=biz_row.get("logo_url"),
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



# ---------- POST /api/v1/profile/apply-supplier ----------


@router.post("/apply-supplier", summary="Apply to become a supplier")
async def apply_to_become_supplier(
    user_id: str = Depends(get_current_user_id),
):
    """
    Submit application to become a verified supplier.
    Requires that the user's business profile is completely filled out.
    """
    user_row = await sb.fetch_user(user_id)
    if user_row and user_row.get("role") == "supplier":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are already a registered and verified supplier.",
        )

    biz_row = await sb.fetch_business(user_id)
    if not biz_row:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Business profile not found. Please complete your business profile (legal name, phone number, address, and coordinates) before applying.",
        )

    missing = []
    if not (biz_row.get("legal_name") or "").strip():
        missing.append("legal_name")
    if not (biz_row.get("phone_number") or "").strip():
        missing.append("phone_number")
    if not (biz_row.get("physical_address") or "").strip():
        missing.append("physical_address")
    if biz_row.get("latitude") is None or biz_row.get("longitude") is None:
        missing.append("location coordinates (latitude/longitude)")

    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Incomplete business profile. The following fields are required: {', '.join(missing)}.",
        )

    return {
        "status": "pending_approval",
        "message": "Your supplier application has been submitted successfully. An administrator will review your business profile and confirm your supplier status.",
        "business": {
            "legal_name": biz_row.get("legal_name"),
            "phone_number": biz_row.get("phone_number"),
            "physical_address": biz_row.get("physical_address"),
            "city": biz_row.get("city"),
        },
    }
