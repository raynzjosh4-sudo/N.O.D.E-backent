"""
Orders router for N.O.D.E. wholesale ordering system.

Endpoints:
- Draft sync:
    GET    /api/v1/orders/drafts
    POST   /api/v1/orders/drafts
    DELETE /api/v1/orders/drafts/{id}
- Supplier incoming:
    GET    /api/v1/orders/supplier/incoming
- Sent wholesale orders:
    POST   /api/v1/orders
    GET    /api/v1/orders
    GET    /api/v1/orders/{order_id}
    GET    /api/v1/orders/{order_id}/pdf
    PATCH  /api/v1/orders/{order_id}/status
    DELETE /api/v1/orders/{order_id}
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import Response

from app.orders.deps import get_current_user_id
from app.orders.schemas import (
    OrderCreateRequest,
    OrderListItem,
    OrderResponse,
    OrderStatus,
    OrderStatusUpdateRequest,
    PaginatedOrders,
)
from app.orders.supabase_client import (
    delete_draft_for_user,
    delete_order,
    fetch_drafts_for_user,
    fetch_order_by_id,
    fetch_orders_for_user,
    fetch_supplier_incoming,
    update_order_status,
    upsert_order,
)
from app.profile.supabase_client import fetch_business, fetch_user
from app.services.email_service import (
    build_order_status_email_html,
    create_in_app_notification,
    send_order_email,
)
from app.services.pdf_generator import _format_ugx, generate_order_pdf

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


async def _dispatch_order_status_notifications(
    order: dict,
    new_status: str,
    action_by_user_id: str,
):
    """
    Background worker:
    1. Fetches buyer & supplier details.
    2. Builds the official N.O.D.E. order spec sheet PDF.
    3. Sends status update email + PDF attachment to the buyer.
    4. Sends confirmation email + PDF attachment to the supplier.
    5. Writes in-app notifications to notifications_table for both parties.
    """
    order_id = str(order.get("order_id") or order.get("id"))
    buyer_id = order.get("user_id")
    supplier_id = order.get("supplier_id")
    total_amount = float(order.get("total_amount") or 0.0)
    total_ugx = _format_ugx(total_amount)

    buyer_user = (await fetch_user(buyer_id)) if buyer_id else {}
    buyer_biz = (await fetch_business(buyer_id)) if buyer_id else {}
    supplier_user = (await fetch_user(supplier_id)) if supplier_id else {}
    supplier_biz = (await fetch_business(supplier_id)) if supplier_id else {}

    # 1. Generate Order PDF
    try:
        pdf_bytes = generate_order_pdf(
            order=order,
            buyer_user=buyer_user,
            buyer_business=buyer_biz,
            supplier_user=supplier_user,
            supplier_business=supplier_biz,
        )
    except Exception as e:
        print(f"Error generating PDF in background: {e}")
        pdf_bytes = None

    # Status formatting
    status_upper = new_status.upper()
    color_map = {
        "NEW": "#00BFFF",
        "PROCESSING": "#3B82F6",
        "CONFIRMED": "#22C55E",
        "COMPLETED": "#22C55E",
        "CANCELLED": "#EF4444",
    }
    status_color = color_map.get(status_upper, "#00BFFF")

    buyer_name = (buyer_user or {}).get("full_name") or "Valued Merchant"
    buyer_email = (buyer_user or {}).get("email")
    supplier_name = (supplier_biz or {}).get("legal_name") or (supplier_user or {}).get("full_name") or "Supplier"
    supplier_email = (supplier_user or {}).get("email")

    pdf_filename = f"NODE_Order_{order_id}.pdf"

    # 2. Buyer Email & In-App Notification
    if buyer_email:
        buyer_html = build_order_status_email_html(
            recipient_name=buyer_name,
            order_id=order_id,
            status_title=f"Order Status: {status_upper}",
            status_color=status_color,
            message=f"Your wholesale order #[{order_id}] has been updated to '{status_upper}' by {supplier_name}. Please inspect the attached PDF spec sheet and verify delivery upon arrival.",
            order_total_ugx=total_ugx,
            is_supplier=False,
        )
        await send_order_email(
            recipient=buyer_email,
            subject=f"N.O.D.E. Order #{order_id} Status: {status_upper}",
            html_content=buyer_html,
            pdf_bytes=pdf_bytes,
            pdf_filename=pdf_filename,
        )

    if buyer_id:
        await create_in_app_notification(
            user_id=buyer_id,
            title=f"Order #{order_id} is now {status_upper}",
            body=f"Supplier {supplier_name} has updated your order status to {status_upper}.",
            category="order",
            metadata={"order_id": order_id, "status": new_status},
        )

    # 3. Supplier Email & In-App Notification
    if supplier_email:
        supplier_html = build_order_status_email_html(
            recipient_name=supplier_name,
            order_id=order_id,
            status_title=f"Order #{order_id} Marked As {status_upper}",
            status_color=status_color,
            message=f"You updated order #[{order_id}] to '{status_upper}'. The buyer has been notified and issued their digital clearance sheet.",
            order_total_ugx=total_ugx,
            is_supplier=True,
        )
        await send_order_email(
            recipient=supplier_email,
            subject=f"Confirmation: Order #{order_id} marked as {status_upper}",
            html_content=supplier_html,
            pdf_bytes=pdf_bytes,
            pdf_filename=pdf_filename,
        )

    if supplier_id and supplier_id != action_by_user_id:
        await create_in_app_notification(
            user_id=supplier_id,
            title=f"Order #{order_id} Status Changed",
            body=f"Order #[{order_id}] status transitioned to {status_upper}.",
            category="order",
            metadata={"order_id": order_id, "status": new_status},
        )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Draft Sync Endpoints (must precede /{order_id})
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/drafts", response_model=List[OrderResponse], summary="Get drafts for user")
async def get_drafts(
    current_user_id: str = Depends(get_current_user_id),
):
    """Fetch all saved order drafts for the current user."""
    drafts = await fetch_drafts_for_user(current_user_id)
    return [OrderResponse(**d) for d in drafts]


@router.post("/drafts", response_model=OrderResponse, summary="Save or upsert draft")
async def save_draft(
    body: OrderCreateRequest,
    current_user_id: str = Depends(get_current_user_id),
):
    """Save or upsert a local draft synced from Flutter SQLite/Drift."""
    now = datetime.now(timezone.utc).isoformat()
    order_data = body.model_dump()
    order_data["user_id"] = current_user_id
    order_data["status"] = OrderStatus.DRAFT.value
    order_data["created_at"] = now
    order_data["updated_at"] = now

    result = await upsert_order(order_data)
    return OrderResponse(**result)


@router.delete("/drafts/{id}", summary="Delete draft")
async def delete_draft(
    id: str,
    current_user_id: str = Depends(get_current_user_id),
):
    """Delete a draft order by ID or order_id."""
    deleted = await delete_draft_for_user(draft_id=id, user_id=current_user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Draft '{id}' not found",
        )
    return {"success": True, "message": "Draft deleted successfully"}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Supplier Incoming Orders (must precede /{order_id})
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/supplier/incoming",
    response_model=PaginatedOrders,
    summary="Get orders where current user is the supplier",
)
async def get_supplier_incoming_orders(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    current_user_id: str = Depends(get_current_user_id),
):
    """Get paginated incoming orders for the current user acting as supplier."""
    result = await fetch_supplier_incoming(
        supplier_id=current_user_id,
        page=page,
        limit=limit,
        status_filter=status_filter,
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


# ─────────────────────────────────────────────────────────────────────────────
# 3. Wholesale Orders (Buyer side)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED, summary="Send order to supplier")
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

    result = await upsert_order(order_data)

    # Trigger background email dispatch to supplier and buyer with PDF
    background_tasks.add_task(
        _dispatch_order_status_notifications,
        order=result,
        new_status="new",
        action_by_user_id=current_user_id,
    )

    return OrderResponse(**result)


@router.get("", response_model=PaginatedOrders, summary="Get my sent orders")
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

    # Trigger email dispatch to both parties with attached PDF and in-app notifications
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
