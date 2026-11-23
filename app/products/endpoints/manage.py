"""
Product publishing, updating, and management endpoints for suppliers and admins.
"""
import logging
import re
import uuid
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.core.deps import require_supplier_or_admin
from app.db.models import SupplierCustomer
from app.db.session import SessionLocal
from app.products.helpers import (
    _build_product_response,
    _resolve_category_id,
    replace_primary_media_url,
)
from app.products.schemas import (
    CategoryCreatePayload,
    PaginatedProducts,
    ProductCreateRequest,
    ProductListItem,
    ProductResponse,
    ProductUpdateRequest,
)
from app.products.supabase_client import (
    delete_product,
    fetch_product_by_id,
    fetch_products,
    insert_product,
    update_product,
)
from app.services.cloud_media_service import decode_base64_image, upload_to_cloudflare
from app.services.email_service import create_in_app_notification, send_order_email

logger = logging.getLogger(__name__)

router = APIRouter()


async def _broadcast_new_product_to_customers(
    supplier_id: str,
    supplier_name: str,
    product_id: str,
    product_name: str,
    brand: str,
    image_url: str,
    srp: float,
):
    """
    Broadcasts email and in-app notifications to all buyers who have previously ordered
    from this supplier when a new product is published.
    """
    try:
        customers = []
        with SessionLocal() as db_session:
            records = (
                db_session.query(SupplierCustomer)
                .filter(SupplierCustomer.supplier_id == str(supplier_id))
                .all()
            )
            # Deduplicate by buyer_email
            seen_emails = set()
            for r in records:
                if r.buyer_email and r.buyer_email.lower() not in seen_emails:
                    seen_emails.add(r.buyer_email.lower())
                    customers.append({
                        "email": r.buyer_email.lower(),
                        "name": r.buyer_name or "Valued Wholesale Buyer",
                        "user_id": r.buyer_user_id,
                    })

        if not customers:
            return

        formatted_price = f"UGX {srp:,.0f}" if srp else "Wholesale Tier Pricing Available"
        specs_link = f"https://node.crimchart.com/product-specs?id={product_id}"

        for c in customers:
            buyer_name = c["name"]
            recipient_email = c["email"]
            buyer_user_id = c["user_id"]

            html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>New Product Arrival</title>
</head>
<body style="margin: 0; padding: 0; background-color: #0D1117; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #E6EDF3;">
  <table width="100%" cellpadding="0" cellspacing="0" style="padding: 24px 12px; background-color: #0D1117;">
    <tr>
      <td align="center">
        <table width="100%" cellpadding="0" cellspacing="0" style="max-width: 600px; background-color: #161B22; border: 1px solid #30363D; border-radius: 12px; overflow: hidden;">
          <tr>
            <td style="padding: 20px 24px; border-bottom: 1px solid #30363D; background-color: #0D1117;">
              <span style="font-weight: 800; font-size: 18px; color: #58A6FF; letter-spacing: 1px;">N.O.D.E. WHOLESALE</span>
            </td>
          </tr>
          {f'<tr><td align="center" style="padding: 0; background: #000;"><img src="{image_url}" alt="{product_name}" style="max-width: 100%; max-height: 320px; object-fit: contain; display: block;" /></td></tr>' if image_url else ''}
          <tr>
            <td style="padding: 28px 24px;">
              <div style="display: inline-block; padding: 4px 10px; background: rgba(56, 139, 253, 0.15); border: 1px solid #388BFD; border-radius: 16px; font-size: 12px; font-weight: 600; color: #58A6FF; margin-bottom: 12px;">
                NEW WHOLESALE CATALOG UPDATE
              </div>
              <h2 style="margin: 0 0 10px 0; font-size: 20px; color: #FFFFFF;">{product_name}</h2>
              <p style="margin: 0 0 16px 0; color: #8B949E; font-size: 14px;">
                Supplier <strong>{supplier_name}</strong> (who you have previously ordered from) has just published a new wholesale listing.
              </p>
              <div style="background-color: #0D1117; border: 1px solid #21262D; border-radius: 8px; padding: 14px 18px; margin-bottom: 24px;">
                <p style="margin: 0 0 6px 0; font-size: 13px; color: #8B949E;">Brand: <strong style="color: #E6EDF3;">{brand or 'Verified Brand'}</strong></p>
                <p style="margin: 0; font-size: 15px; color: #3FB950; font-weight: 700;">Starting Price: {formatted_price}</p>
              </div>
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td align="center">
                    <a href="{specs_link}" target="_blank" style="display: inline-block; padding: 14px 28px; background-color: #238636; color: #FFFFFF; font-weight: 600; font-size: 15px; text-decoration: none; border-radius: 6px;">
                      View Specs & Place Order
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding: 16px 24px; border-top: 1px solid #21262D; text-align: center; color: #6E7681; font-size: 12px;">
              You received this wholesale notification because you previously placed an order with {supplier_name} on N.O.D.E.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

            await send_order_email(
                recipient=recipient_email,
                subject=f"📦 New Arrival from {supplier_name}: {product_name}",
                html_content=html_content,
            )

            if buyer_user_id:
                await create_in_app_notification(
                    user_id=buyer_user_id,
                    title=f"New Arrival: {product_name}",
                    body=f"{supplier_name} published a new wholesale listing. Tap to view specifications.",
                    category="product",
                    metadata={"product_id": product_id},
                )
        logger.info(f"Broadcasted new product {product_id} to {len(customers)} buyers for supplier {supplier_id}")
    except Exception as e:
        logger.warning(f"Error broadcasting new product to customers: {e}")



@router.get("/my-products", response_model=PaginatedProducts, summary="List supplier's own posted products")
async def list_my_products(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search by name or SKU"),
    sort_by: str = Query("created_at"),
    ascending: bool = Query(False),
    current_user: dict = Depends(require_supplier_or_admin),
):
    """Returns only products posted by the currently authenticated supplier."""
    result = await fetch_products(
        supplier_id=current_user["id"],
        search=search,
        page=page,
        limit=limit,
        sort_by=sort_by,
        ascending=ascending,
    )
    items = [ProductListItem(**p) for p in result["products"]]
    total = result["total"]
    return PaginatedProducts(
        data=items,
        total=total,
        page=page,
        limit=limit,
        has_more=(page * limit) < total,
    )


@router.post("/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED, summary="Post/Publish a new product")
async def create_new_product(
    payload: ProductCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_supplier_or_admin),
):
    """Post a new product to the global catalog."""
    caller_role = (current_user.get("role") or "").lower()
    supplier_id = payload.supplier_id if caller_role == "admin" and payload.supplier_id else current_user["id"]

    slug = payload.slug
    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", payload.name.lower()).strip("-")
        slug = f"{slug}-{uuid.uuid4().hex[:6]}"

    product_id = str(uuid.uuid4())

    # Handle image upload to Cloudflare if base64 data provided
    image_url = payload.image_url or ""
    media_urls = list(payload.media_urls or [])
    if payload.image_base64:
        file_bytes, filename, content_type = decode_base64_image(payload.image_base64)
        uploaded_url = await upload_to_cloudflare(
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            folder="products",
        )
        image_url = uploaded_url
        if uploaded_url not in media_urls:
            media_urls.insert(0, uploaded_url)
    elif image_url and not media_urls:
        media_urls = [image_url]

    category_id = await _resolve_category_id(payload.category_id, payload.new_category)

    product_data = {
        "id": product_id,
        "sku": payload.sku,
        "name": payload.name,
        "brand": payload.brand,
        "slug": slug,
        "srp": payload.srp,
        "category_id": category_id,
        "supplier_id": supplier_id,
        "image_url": image_url,
        "media_urls": media_urls,
        "aspect_ratio": payload.aspect_ratio,
        "price_tiers": [t.model_dump() for t in payload.price_tiers],
        "hs_code": payload.hs_code or "",
        "weight_kg": payload.weight_kg or 0.0,
        "volume_cbm": payload.volume_cbm or 0.0,
        "origin_country": payload.origin_country or "",
        "unbs_number": payload.unbs_number or "",
        "denier": payload.denier or "",
        "material": payload.material or "",
        "current_stock": payload.current_stock or 0,
        "lead_time_days": payload.lead_time_days or 0,
        "warehouse_loc": payload.warehouse_loc or "",
        "seo_title": payload.seo_title or payload.name,
        "seo_description": payload.seo_description or "",
        "search_keywords": payload.search_keywords or [],
        "available_colors": [c.model_dump() for c in payload.available_colors],
        "available_sizes": [s.model_dump() for s in payload.available_sizes],
        "available_materials": [m.model_dump() for m in payload.available_materials],
        "variant_label": payload.variant_label or "Size",
        "support_info": payload.support_info.model_dump() if payload.support_info else {},
        "trading_terms": {
            **(payload.trading_terms.model_dump() if payload.trading_terms else {}),
            **({"description": payload.description} if payload.description else {}),
        },
    }

    inserted = await insert_product(product_data)

    # Broadcast new product update to all buyers who previously placed an order with this supplier
    supplier_name = current_user.get("full_name") or current_user.get("email") or "Wholesale Supplier"
    background_tasks.add_task(
        _broadcast_new_product_to_customers,
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        product_id=product_id,
        product_name=payload.name,
        brand=payload.brand or "",
        image_url=image_url,
        srp=float(payload.srp or 0.0),
    )

    row = await fetch_product_by_id(product_id) or inserted
    return _build_product_response(row)


@router.put("/{product_id}", response_model=ProductResponse, summary="Update an existing product")
async def update_existing_product(
    product_id: str,
    payload: ProductUpdateRequest,
    current_user: dict = Depends(require_supplier_or_admin),
):
    """Update a product (only owner supplier or admin)."""
    existing = await fetch_product_by_id(product_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Product '{product_id}' not found")

    caller_role = (current_user.get("role") or "").lower()
    if caller_role != "admin" and existing.get("supplier_id") != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: You are only permitted to edit your own posted products.",
        )

    updates = payload.model_dump(exclude_none=True)

    if "new_category" in updates:
        new_cat_data = updates.pop("new_category")
        if new_cat_data:
            cat_payload = CategoryCreatePayload(**new_cat_data) if isinstance(new_cat_data, dict) else new_cat_data
            updates["category_id"] = await _resolve_category_id(updates.get("category_id"), cat_payload)

    if updates.get("image_base64"):
        file_bytes, filename, content_type = decode_base64_image(updates.pop("image_base64"))
        uploaded_url = await upload_to_cloudflare(
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            folder="products",
        )
        updates["image_url"] = uploaded_url
        current_media = list(updates.get("media_urls") or existing.get("media_urls") or [])
        updates["media_urls"] = replace_primary_media_url(
            current_media,
            existing.get("image_url"),
            uploaded_url,
        )
    elif "image_base64" in updates:
        updates.pop("image_base64", None)

    if updates.get("image_url"):
        current_media = list(updates.get("media_urls") or existing.get("media_urls") or [])
        updates["media_urls"] = replace_primary_media_url(
            current_media,
            existing.get("image_url"),
            updates["image_url"],
        )

    if "price_tiers" in updates:
        updates["price_tiers"] = [t.model_dump() if hasattr(t, "model_dump") else t for t in payload.price_tiers or []]
    if "available_colors" in updates:
        updates["available_colors"] = [c.model_dump() if hasattr(c, "model_dump") else c for c in payload.available_colors or []]
    if "available_sizes" in updates:
        updates["available_sizes"] = [s.model_dump() if hasattr(s, "model_dump") else s for s in payload.available_sizes or []]
    if "available_materials" in updates:
        updates["available_materials"] = [m.model_dump() if hasattr(m, "model_dump") else m for m in payload.available_materials or []]
    if "support_info" in updates and payload.support_info:
        updates["support_info"] = payload.support_info.model_dump()
    if "trading_terms" in updates and payload.trading_terms:
        updates["trading_terms"] = payload.trading_terms.model_dump()

    desc_val = updates.pop("description", None)
    if desc_val is not None:
        tt = dict(updates.get("trading_terms") or existing.get("trading_terms") or {})
        tt["description"] = desc_val
        updates["trading_terms"] = tt

    if updates:
        await update_product(product_id, updates)

    refreshed = await fetch_product_by_id(product_id)
    return _build_product_response(refreshed or existing)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a product")
async def delete_existing_product(
    product_id: str,
    current_user: dict = Depends(require_supplier_or_admin),
):
    """Delete a product (only owner supplier or admin)."""
    existing = await fetch_product_by_id(product_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Product '{product_id}' not found")

    caller_role = (current_user.get("role") or "").lower()
    if caller_role != "admin" and existing.get("supplier_id") != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: You are only permitted to delete your own posted products.",
        )

    deleted = await delete_product(product_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Failed to delete product")
    return None
