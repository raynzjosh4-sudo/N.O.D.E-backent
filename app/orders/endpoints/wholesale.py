"""
Wholesale orders placement, status updates, and PDF download endpoints.
"""
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import Response

from app.orders.deps import get_current_user_id
from app.orders.notifications import _dispatch_order_status_notifications
from app.orders.schemas import (
    OrderCreateRequest,
    OrderListItem,
    OrderResponse,
    OrderStatus,
    OrderStatusUpdateRequest,
    PaginatedOrders,
)
from app.orders.supabase_client import (
    delete_order,
    fetch_order_by_id,
    fetch_orders_for_user,
    update_order_status,
    upsert_order,
)
from app.profile.supabase_client import fetch_business, fetch_user
from app.services.pdf_generator import generate_order_pdf

router = APIRouter()


@router.post("/", response_model=OrderResponse, status_code=status.HTTP_201_CREATED, summary="Send order to supplier")
async def create_order(
    body: OrderCreateRequest,
    background_tasks: BackgroundTasks,
    current_user_id: str = Depends(get_current_user_id),
):
    """Create or upsert a wholesale order sent to a supplier."""
    now = datetime.now(timezone.utc).isoformat()
    order_data = body.model_dump()
    order_data["user_id"] = current_user_id
    order_data["status"] = OrderStatus.NEW.value
    order_data["created_at"] = now
    order_data["updated_at"] = now

    # Ensure buyer email is always resolved for notifications
    if not order_data.get("buyer_email"):
        try:
            u = await fetch_user(current_user_id)
            if u and u.get("email"):
                order_data["buyer_email"] = u["email"]
        except Exception:
            pass

    result = await upsert_order(order_data)

    background_tasks.add_task(
        _dispatch_order_status_notifications,
        order=result,
        new_status="new",
        action_by_user_id=current_user_id,
    )

    return OrderResponse(**result)


@router.get("/", response_model=PaginatedOrders, summary="Get my sent orders")
async def list_my_orders(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user_id: str = Depends(get_current_user_id),
):
    """Get paginated wholesale orders placed by current user."""
    result = await fetch_orders_for_user(
        user_id=current_user_id,
        page=page,
        limit=limit,
    )

    items = [
        OrderListItem(
            order_id=row.get("order_id", ""),
            supplier_id=row.get("supplier_id"),
            business_name=row.get("business_name"),
            total_amount=float(row.get("total_amount") or 0.0),
            status=row.get("status", "new"),
            created_at=row.get("created_at"),
        )
        for row in result["orders"]
    ]
    total = result["total"]

    return PaginatedOrders(
        data=items,
        total=total,
        page=page,
        limit=limit,
        has_more=(page * limit) < total,
    )


@router.post("/pdf/preview", summary="Generate instant PDF spec sheet from raw order data (no auth required)")
async def preview_order_pdf(body: dict[str, Any]):
    """
    Public endpoint — no authentication required.
    Accepts raw order dict (same shape as OrderCreateRequest) and returns a PDF spec sheet.
    Useful for generating spec sheets before an order is saved, or for unauthenticated users.
    """
    from app.services.pdf_generator import generate_order_pdf as _gen

    # Inject a generated order_id if one isn't supplied
    if not body.get("order_id"):
        body["order_id"] = "SPEC-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    if not body.get("created_at"):
        body["created_at"] = datetime.now(timezone.utc).isoformat()

    buyer_user: dict = body.pop("buyer_user", {}) or {}
    buyer_business: dict = body.pop("buyer_business", {}) or {}
    supplier_user: dict = body.pop("supplier_user", {}) or {}
    supplier_business: dict = body.pop("supplier_business", {}) or {}

    if not buyer_user.get("full_name"):
        buyer_user["full_name"] = body.get("business_name") or "Guest Wholesale Buyer"
    if not buyer_business.get("legal_name"):
        buyer_business["legal_name"] = body.get("business_name") or "Spec Sheet Preview"
    if not buyer_business.get("city"):
        buyer_business["city"] = body.get("business_city") or "Kampala"
    if not buyer_business.get("physical_address"):
        buyer_business["physical_address"] = body.get("business_address") or "Kampala Wholesale Hub"
    if not buyer_business.get("phone_number") and body.get("business_phone"):
        buyer_business["phone_number"] = body.get("business_phone")

    pdf_bytes = _gen(
        order=body,
        buyer_user=buyer_user,
        buyer_business=buyer_business,
        supplier_user=supplier_user,
        supplier_business=supplier_business,
    )

    filename = f"NODE_Spec_{body.get('order_id', 'draft')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.get("/{order_id}/pdf", summary="Download official Order Spec Sheet & Invoice PDF")
async def get_order_pdf(
    order_id: str,
    current_user_id: str = Depends(get_current_user_id),
):
    """
    Generates and streams the official N.O.D.E. Order Spec Sheet PDF for direct web/app viewing.
    Accessible by the buyer who placed it, the supplier who received it, or an admin.
    """
    order = await fetch_order_by_id(order_id=order_id, user_id=current_user_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found or unauthorized",
        )

    buyer_id = order.get("user_id")
    supplier_id = order.get("supplier_id")

    buyer_user = (await fetch_user(buyer_id)) if buyer_id else {}
    buyer_biz = (await fetch_business(buyer_id)) if buyer_id else {}
    supplier_user = (await fetch_user(supplier_id)) if supplier_id else {}
    supplier_biz = (await fetch_business(supplier_id)) if supplier_id else {}

    pdf_bytes = generate_order_pdf(
        order=order,
        buyer_user=buyer_user,
        buyer_business=buyer_biz,
        supplier_user=supplier_user,
        supplier_business=supplier_biz,
    )

    filename = f"NODE_Order_{order_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=\"{filename}\"",
        },
    )


@router.get("/{order_id}", response_model=OrderResponse, summary="Get single order")
async def get_order(
    order_id: str,
    current_user_id: str = Depends(get_current_user_id),
):
    """Get single order details by order_id."""
    order = await fetch_order_by_id(order_id=order_id, user_id=current_user_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found",
        )
    return OrderResponse(**order)


@router.patch("/{order_id}/status", response_model=OrderResponse, summary="Update order status")
async def update_status(
    order_id: str,
    body: OrderStatusUpdateRequest,
    background_tasks: BackgroundTasks,
    current_user_id: str = Depends(get_current_user_id),
):
    """Update order status (caller must be buyer/order owner or supplier)."""
    updated = await update_order_status(
        order_id=order_id,
        user_id=current_user_id,
        new_status=body.status.value,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found or you are not authorized to update its status",
        )

    background_tasks.add_task(
        _dispatch_order_status_notifications,
        order=updated,
        new_status=body.status.value,
        action_by_user_id=current_user_id,
    )

    return OrderResponse(**updated)


@router.delete("/{order_id}", summary="Delete/cancel order")
async def remove_order(
    order_id: str,
    current_user_id: str = Depends(get_current_user_id),
):
    """Delete or cancel an order (must be owner of the order)."""
    deleted = await delete_order(order_id=order_id, user_id=current_user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found",
        )
    return {"success": True, "message": "Order deleted successfully"}
