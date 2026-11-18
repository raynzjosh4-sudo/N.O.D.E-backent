"""
Products router — mirrors Flutter's ProductPostsRepository endpoints.

GET /api/v1/products                   → paginated product list
GET /api/v1/products/{product_id}      → full product detail by UUID
GET /api/v1/products/slug/{slug}       → full product detail by slug
GET /api/v1/products/categories        → all categories
GET /api/v1/products/suppliers         → all supplier users
"""
import re
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from app.core.deps import require_supplier_or_admin
from app.services.cloud_media_service import decode_base64_image, upload_to_cloudflare
from app.products.supabase_client import (
    fetch_products,
    fetch_product_by_id,
    fetch_product_by_slug,
    fetch_categories,
    fetch_suppliers,
    insert_product,
    update_product,
    delete_product,
    insert_category,
    update_category,
    fetch_popular_products,
)
from app.products.schemas import (
    CategoryCreatePayload,
    PaginatedProducts,
    ProductListItem,
    ProductResponse,
    ProductCreateRequest,
    ProductUpdateRequest,
    Supplier,
    Location,
    PriceTier,
    ProductColor,
    ProductSize,
    ProductMaterial,
    ProductSupport,
    TradingTerms,
)

router = APIRouter(prefix="/api/v1/products", tags=["products"])


def _build_supplier(raw: dict) -> Supplier:
    """Reconstruct Supplier from raw DB row (mirrors Supplier.fromJson logic)."""
    business = raw.get("business") or {}
    if isinstance(business, list):
        business = business[0] if business else {}

    loc_raw = raw.get("location") or {}
    if isinstance(loc_raw, dict):
        location = Location(
            latitude=loc_raw.get("latitude", 0.0),
            longitude=loc_raw.get("longitude", 0.0),
            addressName=loc_raw.get("addressName", ""),
        )
    else:
        location = Location(
            latitude=float(business.get("latitude") or raw.get("latitude") or 0),
            longitude=float(business.get("longitude") or raw.get("longitude") or 0),
            addressName=business.get("physical_address") or raw.get("physical_address") or "Headquarters",
        )

    return Supplier(
        id=raw.get("id") or raw.get("user_id") or raw.get("supplier_id") or "",
        name=(
            raw.get("name")
            or business.get("legal_name")
            or raw.get("full_name")
            or raw.get("supplier_name")
            or "N.O.D.E Supplier"
        ),
        imageUrl=(
            raw.get("imageUrl")
            or business.get("logo_url")
            or raw.get("profile_pic_url")
            or ""
        ),
        category=raw.get("category") or business.get("industry_group") or "General",
        location=location,
    )


def _build_product_response(row: dict) -> ProductResponse:
    """
    Map a raw Supabase products_table row → ProductResponse.
    Matches ProductModel.fromJson() field-by-field.
    """
    # Supplier — may be embedded object or flat supplier_id
    supplier_raw = row.get("supplier") or {}
    if not supplier_raw and row.get("supplier_id"):
        supplier_raw = {
            "id": row["supplier_id"],
            "supplier_name": row.get("supplier_name"),
        }
    supplier = _build_supplier(supplier_raw) if supplier_raw else Supplier(
        id=row.get("supplier_id", ""),
        name=row.get("supplier_name", "N.O.D.E Supplier"),
    )

    # price_tiers
    tiers_raw = row.get("price_tiers") or []
    price_tiers = [
        PriceTier(
            minQuantity=t.get("minQuantity", 0),
            price=float(t.get("price", 0)),
            label=t.get("label"),
        )
        for t in tiers_raw
    ]

    # available_colors — Flutter uses {name, hexCode}
    colors = [
        ProductColor(name=c.get("name", ""), hexCode=c.get("hexCode", ""))
        for c in (row.get("available_colors") or [])
    ]

    # available_sizes — Flutter uses {name, abbreviation}
    sizes = [
        ProductSize(name=s.get("name", ""), abbreviation=s.get("abbreviation", ""))
        for s in (row.get("available_sizes") or [])
    ]

    # available_materials — Flutter uses {name, imageUrl}
    materials = [
        ProductMaterial(name=m.get("name", ""), imageUrl=m.get("imageUrl"))
        for m in (row.get("available_materials") or [])
    ]

    # support — DB may store as support_info or support
    support_raw = row.get("support_info") or row.get("support") or {}
    support = ProductSupport(
        whatsapp=support_raw.get("whatsapp", "+256 000 000 000"),
        phone=support_raw.get("phone", "+256 000 000 000"),
        email=support_raw.get("email", "support@node.app"),
    ) if support_raw else ProductSupport()

    # trading_terms
    tt_raw = row.get("trading_terms") or {}
    trading_terms = TradingTerms(
        id=tt_raw.get("id", "tt_default"),
        content=tt_raw.get("content", "Standard trading terms apply for all wholesale transactions."),
        moq=tt_raw.get("moq"),
        warranty=tt_raw.get("warranty"),
        paymentTerms=tt_raw.get("paymentTerms"),
        deliveryTerms=tt_raw.get("deliveryTerms"),
    ) if tt_raw else TradingTerms()

    # de-duplicate media_urls (mirrors Flutter's .toSet().toList())
    media_urls = list(dict.fromkeys(row.get("media_urls") or []))

    return ProductResponse(
        id=row.get("id", ""),
        sku=row.get("sku", ""),
        name=row.get("name", ""),
        brand=row.get("brand", ""),
        price_tiers=price_tiers,
        srp=float(row.get("srp") or 0),
        hs_code=row.get("hs_code", ""),
        weight_kg=float(row.get("weight_kg") or 0),
        volume_cbm=float(row.get("volume_cbm") or 0),
        origin_country=row.get("origin_country", ""),
        unbs_number=row.get("unbs_number", ""),
        denier=row.get("denier", ""),
        material=row.get("material", ""),
        supplier=supplier,
        category_id=row.get("category_id", ""),
        current_stock=int(row.get("current_stock") or 0),
        lead_time_days=int(row.get("lead_time_days") or 0),
        warehouse_loc=row.get("warehouse_loc", ""),
        seo_title=row.get("seo_title", ""),
        seo_description=row.get("seo_description", ""),
        search_keywords=list(row.get("search_keywords") or []),
        slug=row.get("slug", ""),
        image_url=row.get("image_url", ""),
        media_urls=media_urls,
        aspect_ratio=float(row.get("aspect_ratio") or 1.0),
        available_colors=colors,
        available_sizes=sizes,
        available_materials=materials,
        variant_label=row.get("variant_label", "Size"),
        support=support,
        trading_terms=trading_terms,
    )


# ─────────────────────────── ENDPOINTS ───────────────────────────

@router.get("", response_model=PaginatedProducts, summary="List products")
async def list_products(
    supplier_id: Optional[str] = Query(None, description="Filter by supplier UUID"),
    category_id: Optional[str] = Query(None, description="Filter by category UUID"),
    search: Optional[str] = Query(None, description="Search by name or SKU"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    sort_by: str = Query("created_at", description="Column to sort by"),
    ascending: bool = Query(False),
):
    result = await fetch_products(
        supplier_id=supplier_id,
        category_id=category_id,
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


@router.get("/categories", summary="List all categories")
async def list_categories():
    return await fetch_categories()


@router.post("/categories", status_code=status.HTTP_201_CREATED, summary="Create a category or subcategory with Cloudflare image")
async def create_product_category(
    payload: CategoryCreatePayload,
    current_user: dict = Depends(require_supplier_or_admin),
):
    """
    Create a new category or subcategory.
    If image_base64 is provided, uploads it to Cloudflare R2 (folder='categories').
    - For root categories: level=0, parent_id=None
    - For subcategories: level=1 (or higher), parent_id set to parent's category ID.
    """
    image_url = payload.image_url or ""
    if payload.image_base64:
        file_bytes, filename, content_type = decode_base64_image(payload.image_base64)
        image_url = await upload_to_cloudflare(
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            folder="categories",
        )

    return await insert_category(
        name=payload.name,
        parent_id=payload.parent_id,
        image_url=image_url,
        level=payload.level,
    )


@router.post("/categories/with-image", status_code=status.HTTP_201_CREATED, summary="Create category/subcategory with direct image file upload")
async def create_category_with_image(
    name: str = Form(..., description="Category name"),
    parent_id: Optional[str] = Form(None, description="Parent category ID (omit for root level)"),
    level: int = Form(0, description="Hierarchy level: 0 for root, 1 for subcategory"),
    file: Optional[UploadFile] = File(None, description="Category cover image file"),
    current_user: dict = Depends(require_supplier_or_admin),
):
    """
    Create a new category or subcategory with a direct multipart image file upload.
    Uploads the file directly to Cloudflare R2 (folder='categories') and stores the public CDN URL.
    """
    image_url = ""
    if file:
        file_bytes = await file.read()
        if file_bytes:
            image_url = await upload_to_cloudflare(
                file_bytes=file_bytes,
                filename=file.filename or f"cat_{name}.jpg",
                content_type=file.content_type or "image/jpeg",
                folder="categories",
            )

    return await insert_category(
        name=name.strip(),
        parent_id=parent_id.strip() if parent_id else None,
        image_url=image_url,
        level=level,
    )


@router.post("/categories/{category_id}/image", summary="Upload/Update category cover image")
@router.put("/categories/{category_id}/image", summary="Upload/Update category cover image")
async def upload_category_image(
    category_id: str,
    file: UploadFile = File(..., description="Image file to set as category cover"),
    current_user: dict = Depends(require_supplier_or_admin),
):
    """
    Upload or replace the cover image for an existing category or subcategory.
    Uploads directly to Cloudflare R2 and updates image_url in categories_table.
    """
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")

    uploaded_url = await upload_to_cloudflare(
        file_bytes=file_bytes,
        filename=file.filename or f"{category_id}.jpg",
        content_type=file.content_type or "image/jpeg",
        folder="categories",
    )

    updated = await update_category(category_id, {"image_url": uploaded_url})
    return {
        "id": category_id,
        "image_url": uploaded_url,
        "category": updated,
    }



async def _resolve_category_id(
    category_id: Optional[str],
    new_category: Optional[CategoryCreatePayload],
) -> str:
    """
    Resolves category ID: if new_category is provided, creates the category/subcategory
    (with Cloudflare R2 image upload) and returns its generated category ID.
    """
    if new_category:
        cat_image_url = new_category.image_url or ""
        if new_category.image_base64:
            file_bytes, filename, content_type = decode_base64_image(new_category.image_base64)
            cat_image_url = await upload_to_cloudflare(
                file_bytes=file_bytes,
                filename=filename,
                content_type=content_type,
                folder="categories",
            )
        created_cat = await insert_category(
            name=new_category.name,
            parent_id=new_category.parent_id,
            image_url=cat_image_url,
            level=new_category.level,
        )
        return created_cat.get("id", category_id or "cat_1")
    return category_id or "cat_1"



@router.get("/suppliers", summary="List all suppliers")
async def list_suppliers():
    return await fetch_suppliers()


@router.get("/popular", response_model=list[ProductResponse], summary="Get popular products for home screen explore section")
async def get_popular_products(
    category_id: Optional[str] = Query(None, description="Optional category UUID to filter by"),
    limit: int = Query(15, ge=1, le=50, description="Number of products to return (default 15)"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """
    Returns popular and trending products for the home screen explore section.
    Directly feeds Flutter's popularProductsProvider and PopularSection widget.
    """
    rows = await fetch_popular_products(
        category_id=category_id,
        limit=limit,
        offset=offset,
    )
    return [_build_product_response(r) for r in rows]



@router.get("/slug/{slug}", response_model=ProductResponse, summary="Get product by slug")
async def get_product_by_slug(slug: str):
    row = await fetch_product_by_slug(slug)
    if not row:
        raise HTTPException(status_code=404, detail=f"Product with slug '{slug}' not found")
    return _build_product_response(row)


@router.get("/my-products", response_model=PaginatedProducts, summary="List supplier's own posted products")
async def list_my_products(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search by name or SKU"),
    sort_by: str = Query("created_at"),
    ascending: bool = Query(False),
    current_user: dict = Depends(require_supplier_or_admin),
):
    """
    Returns only products posted by the currently authenticated supplier.
    """
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


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED, summary="Post/Publish a new product")
async def create_new_product(
    payload: ProductCreateRequest,
    current_user: dict = Depends(require_supplier_or_admin),
):
    """
    Post a new product to the global catalog.
    Suppliers can only post under their own supplier_id.
    """
    # Enforce supplier_id for suppliers
    caller_role = (current_user.get("role") or "").lower()
    supplier_id = payload.supplier_id if caller_role == "admin" and payload.supplier_id else current_user["id"]

    # Generate slug if omitted
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

    # Resolve or create category (with Cloudflare image if new)
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
        "trading_terms": payload.trading_terms.model_dump() if payload.trading_terms else {},
    }

    inserted = await insert_product(product_data)
    # Re-fetch or build response
    row = await fetch_product_by_id(product_id) or inserted
    return _build_product_response(row)


@router.put("/{product_id}", response_model=ProductResponse, summary="Update an existing product")
async def update_existing_product(
    product_id: str,
    payload: ProductUpdateRequest,
    current_user: dict = Depends(require_supplier_or_admin),
):
    """
    Update a product.
    Suppliers can only edit products they personally posted.
    """
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

    # Resolve or create category if new_category provided
    if "new_category" in updates:
        new_cat_data = updates.pop("new_category")
        if new_cat_data:
            cat_payload = CategoryCreatePayload(**new_cat_data) if isinstance(new_cat_data, dict) else new_cat_data
            updates["category_id"] = await _resolve_category_id(updates.get("category_id"), cat_payload)

    # Handle image upload to Cloudflare if base64 data provided
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
        if uploaded_url not in current_media:
            current_media.insert(0, uploaded_url)
        updates["media_urls"] = current_media
    elif "image_base64" in updates:
        updates.pop("image_base64", None)

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

    if updates:
        await update_product(product_id, updates)

    refreshed = await fetch_product_by_id(product_id)
    return _build_product_response(refreshed or existing)


@router.post("/{product_id}/image", response_model=ProductResponse, summary="Upload image file to Cloudflare and update product")
@router.put("/{product_id}/image", response_model=ProductResponse, summary="Upload image file to Cloudflare and update product")
async def upload_product_image_file(
    product_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(require_supplier_or_admin),
):
    """
    Receives an image file from the user (multipart/form-data), uploads it to Cloudflare R2,
    and updates the product's image_url and media_urls in Supabase DB.
    """
    existing = await fetch_product_by_id(product_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Product '{product_id}' not found")

    caller_role = (current_user.get("role") or "").lower()
    if caller_role != "admin" and existing.get("supplier_id") != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: You are only permitted to edit your own posted products.",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")

    # 1. Upload to Cloudflare R2
    uploaded_url = await upload_to_cloudflare(
        file_bytes=file_bytes,
        filename=file.filename or f"{product_id}.jpg",
        content_type=file.content_type or "image/jpeg",
        folder="products",
    )

    # 2. Update image_url and media_urls in DB
    current_media = list(existing.get("media_urls") or [])
    if uploaded_url not in current_media:
        current_media.insert(0, uploaded_url)

    updates = {
        "image_url": uploaded_url,
        "media_urls": current_media,
    }
    await update_product(product_id, updates)

    refreshed = await fetch_product_by_id(product_id)
    return _build_product_response(refreshed or existing)


@router.post("/upload-image", summary="Upload general image to Cloudflare R2")
async def upload_general_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_supplier_or_admin),
):
    """
    Standalone upload: uploads an image file to Cloudflare R2 and returns its public CDN URL.
    """
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")

    uploaded_url = await upload_to_cloudflare(
        file_bytes=file_bytes,
        filename=file.filename or "image.jpg",
        content_type=file.content_type or "image/jpeg",
        folder="products",
    )
    return {
        "url": uploaded_url,
        "filename": file.filename,
        "content_type": file.content_type,
    }


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a product")
async def delete_existing_product(
    product_id: str,
    current_user: dict = Depends(require_supplier_or_admin),
):
    """
    Delete a product.
    Suppliers can only delete products they personally posted.
    """
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


@router.get("/{product_id}", response_model=ProductResponse, summary="Get product by ID")
async def get_product(product_id: str):
    row = await fetch_product_by_id(product_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found")
    return _build_product_response(row)
