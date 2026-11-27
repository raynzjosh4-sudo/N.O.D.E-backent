"""
PDF Document assembler for official N.O.D.E. Order Spec Sheets.
"""
import io
from datetime import datetime, timezone
from typing import Any, Optional
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Spacer

from app.services.pdf.components import (
    build_consolidation_table,
    build_footer,
    build_header,
    build_merchant_bar,
    build_verification_qr_table,
)
from app.services.pdf.matrix import build_entry_flowables


def consolidate_order_entries(entries: list[Any]) -> list[dict[str, Any]]:
    """
    Consolidates order entries so that multiple color/size variants of the same product
    are combined into a single entry with a unified `confirmedGroups` list.
    This ensures ReportLab generates ONE clean product block with a full color-size matrix table,
    rather than repeating product headers, photos, and specs for every individual color.
    """
    if not entries:
        return []

    grouped: dict[str, dict[str, Any]] = {}
    order_keys: list[str] = []

    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        entry = dict(raw_entry)
        prod = entry.get("savedProduct", {}).get("product") or entry.get("product") or {}
        prod_id = str(entry.get("product_id") or prod.get("id") or entry.get("name") or "item").strip()
        key = prod_id if prod_id else str(entry.get("name", "item")).strip()

        # Normalize incoming color/size groups from this entry
        entry_groups = list(entry.get("confirmedGroups") or [])
        if not entry_groups:
            sq = entry.get("size_quantities") or entry.get("sizeQtys") or {}
            if sq:
                color_label = entry.get("color") or "Standard"
                color_hex = entry.get("colorHex") or "#12BBF0"
                entry_groups = [{"color": {"label": color_label, "color": color_hex}, "sizeQtys": sq}]

        if key not in grouped:
            order_keys.append(key)
            merged_entry = dict(entry)
            merged_entry["confirmedGroups"] = [
                {
                    "color": dict(g.get("color") or {"label": g.get("colorName") or "Standard", "color": g.get("colorHex") or "#12BBF0"}),
                    "sizeQtys": dict(g.get("sizeQtys") or g.get("size_quantities") or {}),
                }
                for g in entry_groups
            ]
            grouped[key] = merged_entry
        else:
            merged_entry = grouped[key]
            existing_groups = merged_entry.setdefault("confirmedGroups", [])
            for new_g in entry_groups:
                new_color_label = str((new_g.get("color") or {}).get("label") or new_g.get("colorName") or "Standard").strip()
                new_sq = new_g.get("sizeQtys") or new_g.get("size_quantities") or {}

                matched = False
                for ex_g in existing_groups:
                    ex_color_label = str((ex_g.get("color") or {}).get("label") or ex_g.get("colorName") or "Standard").strip()
                    if ex_color_label.lower() == new_color_label.lower():
                        ex_sq = ex_g.setdefault("sizeQtys", ex_g.get("size_quantities") or {})
                        for sz, qty in new_sq.items():
                            ex_sq[sz] = int(ex_sq.get(sz, 0) or 0) + int(qty or 0)
                        matched = True
                        break
                if not matched:
                    existing_groups.append({
                        "color": dict(new_g.get("color") or {"label": new_color_label, "color": (new_g.get("color") or {}).get("color") or "#12BBF0"}),
                        "sizeQtys": dict(new_sq),
                    })

    # Recalculate units and pricing for all consolidated entries
    results = []
    for k in order_keys:
        item = grouped[k]
        c_groups = item.get("confirmedGroups") or []
        total_units_sum = sum(
            sum(int(v or 0) for v in (g.get("sizeQtys") or g.get("size_quantities") or {}).values())
            for g in c_groups
        )
        if total_units_sum > 0:
            item["total_units"] = total_units_sum
            item["totalUnits"] = total_units_sum

            # Calculate tiered wholesale price based on total product units
            prod = item.get("savedProduct", {}).get("product") or item.get("product") or {}
            price_tiers = prod.get("price_tiers") or []
            unit_price = float(item.get("unit_price") or 0.0)
            if unit_price <= 0.0 or price_tiers:
                base_srp = float(prod.get("srp") or unit_price or 0.0)
                calc_price = base_srp
                for t in sorted(price_tiers, key=lambda x: x.get("minQuantity", 0), reverse=True):
                    if total_units_sum >= t.get("minQuantity", 0):
                        calc_price = float(t.get("price", calc_price))
                        break
                if calc_price > 0:
                    unit_price = calc_price
            item["unit_price"] = unit_price
            item["subtotal"] = unit_price * total_units_sum

        results.append(item)

    return results


def generate_order_pdf(
    order: dict[str, Any],
    buyer_user: Optional[dict[str, Any]] = None,
    buyer_business: Optional[dict[str, Any]] = None,
    supplier_user: Optional[dict[str, Any]] = None,
    supplier_business: Optional[dict[str, Any]] = None,
) -> bytes:
    """
    Generate an official N.O.D.E. Order Spec Sheet / Invoice PDF.
    Returns the binary content as bytes.
    """
    order_id = str(order.get("order_id") or order.get("id") or "ORD-UNKNOWN")
    created_at_str = order.get("created_at") or datetime.now(timezone.utc).isoformat()
    try:
        dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        date_str = dt.strftime("%B %d, %Y")
    except Exception:
        date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    buyer_user = buyer_user or {}
    buyer_business = buyer_business or {}
    supplier_business = supplier_business or {}

    buffer = io.BytesIO()

    # Page setup - A4 with 30pt horizontal margin
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=30,
        rightMargin=30,
        topMargin=25,
        bottomMargin=35,
    )

    page_width = A4[0] - 60  # total usable content width (~535pt)
    story = []

    # 1. Header (Dark Executive Bleed)
    story.append(build_header(date_str, page_width))

    # 2. Merchant Identity Bar
    story.append(build_merchant_bar(buyer_user, buyer_business, page_width))
    story.append(Spacer(1, 14))

    # 3. Entries / Line Items Breakdown (Consolidated by product)
    raw_entries = order.get("entries_json") or []
    if isinstance(raw_entries, dict):
        raw_entries = [raw_entries]

    entries = consolidate_order_entries(raw_entries)

    grand_total_calc = 0.0
    total_units_calc = 0
    default_business_name = str(order.get("business_name") or "")

    for entry in entries:
        flowables, entry_total, units = build_entry_flowables(
            entry=entry,
            supplier_business=supplier_business,
            default_business_name=default_business_name,
            page_width=page_width,
        )
        story.extend(flowables)
        grand_total_calc += entry_total
        total_units_calc += units

    # 4. Order Consolidation Summary / Grand Total
    grand_total_display = float(order.get("total_amount") or grand_total_calc)
    story.append(build_consolidation_table(total_units_calc, grand_total_display, page_width))
    story.append(Spacer(1, 14))

    # 5. Delivery Verification Clearance (QR Code Block)
    story.append(build_verification_qr_table(order_id, page_width))
    story.append(Spacer(1, 12))

    # 6. Footer
    story.extend(build_footer())

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
