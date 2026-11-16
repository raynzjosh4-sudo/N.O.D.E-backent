"""
Supabase REST client for products.
Mirrors Flutter's ProductPostsRepository queries exactly.
"""
import json
import httpx
from typing import Optional

from app.core.config import supabase_url, supabase_anon_key

SUPABASE_URL = supabase_url()
SUPABASE_KEY = supabase_anon_key()

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def _url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"


async def fetch_products(
    *,
    supplier_id: Optional[str] = None,
    category_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
    sort_by: str = "created_at",
    ascending: bool = False,
) -> dict:
    """
    Fetch paginated lightweight product list.
    Mirrors Flutter admin/supplier split + supplier_name join.
    """
    offset = (page - 1) * limit

    params: dict = {
        "select": "id,name,sku,brand,srp,image_url,created_at,supplier_id",
        "order": f"{sort_by}.{'asc' if ascending else 'desc'}",
        "limit": limit,
        "offset": offset,
    }

    if supplier_id:
        params["supplier_id"] = f"eq.{supplier_id}"

    if category_id:
        params["category_id"] = f"eq.{category_id}"

    if search:
        # Full-text search on name and sku
        params["or"] = f"(name.ilike.*{search}*,sku.ilike.*{search}*)"

    headers = {**HEADERS, "Prefer": "count=exact"}

    async with httpx.AsyncClient() as client:
        resp = await client.get(_url("products_table"), params=params, headers=headers)
        resp.raise_for_status()

    products: list = resp.json()
    total_count = int(resp.headers.get("content-range", "0/0").split("/")[-1] or 0)

    if not products:
        return {"products": [], "total": total_count}

    # Fetch supplier names (mirrors _fetchSupplierNames)
    supplier_ids = list({p["supplier_id"] for p in products if p.get("supplier_id")})
    supplier_names: dict[str, str] = {}

    if supplier_ids:
        ids_param = json.dumps(supplier_ids)
        async with httpx.AsyncClient() as client:
            sr = await client.get(
                _url("users_table"),
                params={"select": "id,full_name", "id": f"in.({','.join(supplier_ids)})"},
                headers=HEADERS,
            )
            if sr.status_code == 200:
                for row in sr.json():
                    if row.get("id") and row.get("full_name"):
                        supplier_names[row["id"]] = row["full_name"]

    for p in products:
        p["supplier_name"] = supplier_names.get(p.get("supplier_id", ""), None)

    return {"products": products, "total": total_count}


async def fetch_product_by_id(product_id: str) -> Optional[dict]:
    """
    Fetch full product detail by UUID.
    Mirrors Flutter getProductById() — selects all columns + enriches supplier_name.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _url("products_table"),
            params={"id": f"eq.{product_id}", "limit": 1},
            headers=HEADERS,
        )
        resp.raise_for_status()

    rows = resp.json()
    if not rows:
        return None

    product = rows[0]
    supplier_id = product.get("supplier_id")
    if supplier_id:
        async with httpx.AsyncClient() as client:
            sr = await client.get(
                _url("users_table"),
                params={"select": "id,full_name", "id": f"eq.{supplier_id}"},
                headers=HEADERS,
            )
            if sr.status_code == 200 and sr.json():
                product["supplier_name"] = sr.json()[0].get("full_name")

    return product


async def fetch_product_by_slug(slug: str) -> Optional[dict]:
    """Fetch full product detail by slug."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _url("products_table"),
            params={"slug": f"eq.{slug}", "limit": 1},
            headers=HEADERS,
        )
        resp.raise_for_status()

    rows = resp.json()
    if not rows:
        return None

    product = rows[0]
    supplier_id = product.get("supplier_id")
    if supplier_id:
        async with httpx.AsyncClient() as client:
            sr = await client.get(
                _url("users_table"),
                params={"select": "id,full_name", "id": f"eq.{supplier_id}"},
                headers=HEADERS,
            )
            if sr.status_code == 200 and sr.json():
                product["supplier_name"] = sr.json()[0].get("full_name")

    return product


async def fetch_categories() -> list:
    """Fetch all categories ordered by name."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _url("categories_table"),
            params={"select": "id,name,parent_id,level", "order": "name.asc"},
            headers=HEADERS,
        )
        resp.raise_for_status()
    return resp.json()


async def fetch_suppliers() -> list:
    """Fetch all supplier users ordered by full_name."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _url("users_table"),
            params={"select": "id,full_name", "role": "eq.supplier", "order": "full_name.asc"},
            headers=HEADERS,
        )
        resp.raise_for_status()
    return resp.json()



async def insert_product(product_data: dict) -> dict:
    """Insert a new product record into products_table."""
    headers = {**HEADERS, "Prefer": "return=representation"}
    async with httpx.AsyncClient() as client:
        resp = await client.post(_url("products_table"), json=product_data, headers=headers)
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else product_data


async def update_product(product_id: str, updates: dict) -> dict:
    """Update fields on an existing product in products_table."""
    headers = {**HEADERS, "Prefer": "return=representation"}
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            _url("products_table"),
            params={"id": f"eq.{product_id}"},
            json=updates,
            headers=headers,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else updates


async def delete_product(product_id: str) -> bool:
    """Delete a product from products_table."""
    headers = {**HEADERS, "Prefer": "return=representation"}
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            _url("products_table"),
            params={"id": f"eq.{product_id}"},
            headers=headers,
        )
        resp.raise_for_status()
        return len(resp.json()) > 0
