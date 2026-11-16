"""
Products router — mirrors Flutter's ProductPostsRepository endpoints.

GET /api/v1/products                   → paginated product list
GET /api/v1/products/{product_id}      → full product detail by UUID
GET /api/v1/products/slug/{slug}       → full product detail by slug
GET /api/v1/products/categories        → all categories
GET /api/v1/products/suppliers         → all supplier users
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from app.products.supabase_client import (
    fetch_products,
    fetch_product_by_id,
    fetch_product_by_slug,
    fetch_categories,
    fetch_suppliers,
)
from app.products.schemas import (
    PaginatedProducts,
    ProductListItem,
    ProductResponse,
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


@router.get("/suppliers", summary="List all suppliers")
async def list_suppliers():
    return await fetch_suppliers()


@router.get("/slug/{slug}", response_model=ProductResponse, summary="Get product by slug")
async def get_product_by_slug(slug: str):
    row = await fetch_product_by_slug(slug)
    if not row:
        raise HTTPException(status_code=404, detail=f"Product with slug '{slug}' not found")
    return _build_product_response(row)


@router.get("/{product_id}", response_model=ProductResponse, summary="Get product by ID")
async def get_product(product_id: str):
    row = await fetch_product_by_id(product_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found")
    return _build_product_response(row)
