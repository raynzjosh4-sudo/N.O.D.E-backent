"""
Pydantic schemas matching Flutter's ProductModel.fromJson() exactly.
"""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel


class Location(BaseModel):
    latitude: float = 0.0
    longitude: float = 0.0
    addressName: str = ""


class Supplier(BaseModel):
    id: str = ""
    name: str = ""
    imageUrl: str = ""
    category: str = ""
    location: Location = Location()


class PriceTier(BaseModel):
    minQuantity: int
    price: float
    label: Optional[str] = None


class ProductColor(BaseModel):
    name: str
    hexCode: str


class ProductSize(BaseModel):
    name: str
    abbreviation: str


class ProductMaterial(BaseModel):
    name: str
    imageUrl: Optional[str] = None


class ProductSupport(BaseModel):
    whatsapp: str = "+256 000 000 000"
    phone: str = "+256 000 000 000"
    email: str = "support@node.app"


class TradingTerms(BaseModel):
    id: str = "tt_default"
    content: str = "Standard trading terms apply for all wholesale transactions."
    moq: Optional[str] = None
    warranty: Optional[str] = None
    paymentTerms: Optional[str] = None
    deliveryTerms: Optional[str] = None


class ProductResponse(BaseModel):
    id: str
    sku: str
    name: str
    brand: str
    price_tiers: List[PriceTier] = []
    srp: float = 0.0
    hs_code: str = ""
    weight_kg: float = 0.0
    volume_cbm: float = 0.0
    origin_country: str = ""
    unbs_number: str = ""
    denier: str = ""
    material: str = ""
    supplier: Supplier = Supplier()
    category_id: str = ""
    current_stock: int = 0
    lead_time_days: int = 0
    warehouse_loc: str = ""
    seo_title: str = ""
    seo_description: str = ""
    search_keywords: List[str] = []
    slug: str = ""
    image_url: str = ""
    media_urls: List[str] = []
    aspect_ratio: float = 1.0
    available_colors: List[ProductColor] = []
    available_sizes: List[ProductSize] = []
    available_materials: List[ProductMaterial] = []
    variant_label: str = "Size"
    support: ProductSupport = ProductSupport()
    trading_terms: TradingTerms = TradingTerms()


class ProductListItem(BaseModel):
    """Lightweight model for list view — matches Flutter inventory list query."""
    id: str
    name: str
    sku: str
    brand: str
    srp: float
    image_url: str
    created_at: Optional[str] = None
    supplier_id: Optional[str] = None
    supplier_name: Optional[str] = None


class PaginatedProducts(BaseModel):
    data: List[ProductListItem]
    total: int
    page: int
    limit: int
    has_more: bool
