from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from app.profile.deps import get_current_token_payload
from app.saved_items.schemas import (
    SavedItemCreate,
    SavedItemResponse,
    SavedItemCheckResponse,
)
from app.saved_items.supabase_client import (
    get_user_saved_items,
    upsert_saved_item,
    delete_saved_item,
    check_item_saved,
)

router = APIRouter(prefix="/api/v1/saved-items", tags=["saved-items"])


@router.get("", response_model=list[SavedItemResponse], summary="List all saved items")
async def list_saved_items(user_data: dict[str, Any] = Depends(get_current_token_payload)):
    return await get_user_saved_items(user_data["user_id"], email=user_data.get("email"))


@router.post("", response_model=SavedItemResponse, summary="Save item to wishlist")
async def save_item(
    payload: SavedItemCreate,
    user_data: dict[str, Any] = Depends(get_current_token_payload),
):
    saved = await upsert_saved_item(
        user_id=user_data["user_id"],
        product_id=payload.product_id,
        quantity=payload.quantity,
        selected_color=payload.selected_color,
        selected_size=payload.selected_size,
        product_snapshot=payload.product_snapshot,
        email=user_data.get("email"),
    )
    return saved


@router.delete("/{saved_item_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove saved item")
async def remove_saved_item(
    saved_item_id: str,
    user_data: dict[str, Any] = Depends(get_current_token_payload),
):
    deleted = await delete_saved_item(saved_item_id, user_data["user_id"], email=user_data.get("email"))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved item not found")
    return None


@router.get("/{product_id}/check", response_model=SavedItemCheckResponse, summary="Check if product is saved")
async def check_saved(
    product_id: str,
    user_data: dict[str, Any] = Depends(get_current_token_payload),
):
    is_saved, item_id = await check_item_saved(user_data["user_id"], product_id, email=user_data.get("email"))
    return SavedItemCheckResponse(is_saved=is_saved, saved_item_id=item_id)
