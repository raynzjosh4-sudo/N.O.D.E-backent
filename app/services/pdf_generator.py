"""
N.O.D.E. Order Spec Sheet & Invoice PDF Generator.
Faithfully mirrors Flutter's OrderPdfService (lib/core/pdf/order_pdf_service.dart) & PdfTheme.
"""
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import qrcode
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    Image,
    PageBreak,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Colors matching PdfTheme (constants/pdf_theme.dart)
PRIMARY_COLOR = HexColor("#00BFFF")       # Brand sky blue
DARK_BG = HexColor("#0D0D1A")             # Executive Navy/Black
BODY_TEXT = HexColor("#1A1A2E")           # Dark Charcoal
HEADER_TEXT = white                       # White
SUBTLE_COLOR = HexColor("#71717A")        # Zinc/Grey
BORDER_COLOR = HexColor("#E4E4E7")        # Subtle borders
BORDER_DARK = HexColor("#3F3F46")         # Dark border for cards
LIGHT_BG = HexColor("#FAFAFA")            # Very light grey for cards
CARD_FILL = HexColor("#F4F4F5")
ACCENT_GREEN = HexColor("#22C55E")
ACCENT_RED = HexColor("#EF4444")

STATIC_DIR = Path(__file__).parent.parent / "static"
NODE_ICON_PATH = STATIC_DIR / "nodeicon.png"
NEXUS_ICON_PATH = STATIC_DIR / "nexus.png"


def _format_ugx(val: float) -> str:
    try:
        return f"UGX {int(round(float(val))):,}"
    except (ValueError, TypeError):
        return f"UGX {val}"


def _make_qr_image(data: str, size: float = 70.0) -> Image:
    """Generate scannable QR code in-memory."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=1,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Image(buf, width=size, height=size)


class NumberedCanvas:
    """Canvas that captures total pages and draws running headers and footers."""
    pass


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
    created_at_str = order.get("created_at") or datetime.utcnow().isoformat()
    try:
        dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        date_str = dt.strftime("%B %d, %Y")
    except Exception:
        date_str = datetime.utcnow().strftime("%B %d, %Y")

    buyer_user = buyer_user or {}
    buyer_business = buyer_business or {}
    supplier_user = supplier_user or {}
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

    # ─────────────────────────────────────────────────────────────
    # 1. HEADER (Dark Executive Bleed)
    # ─────────────────────────────────────────────────────────────
    header_logo_cells = []
    if NODE_ICON_PATH.exists():
        logo_img = Image(str(NODE_ICON_PATH), width=28, height=28)
        header_logo_cells.append(logo_img)
    else:
        header_logo_cells.append(Paragraph("<font color='#00BFFF'>●</font>", ParagraphStyle("Bullet", fontSize=18)))

    brand_para = Paragraph(
        "<font size='18' color='#FFFFFF'><b>N.O.D.E.</b></font><br/>"
        "<font size='6.5' color='#71717A'><b>NATIONAL ORDER &amp; DISTRIBUTION EXCHANGE</b></font>",
        ParagraphStyle("BrandHead", leading=11),
    )

    badge_meta_para = Paragraph(
        f"<para align='right'>"
        f"<font size='7' color='#00BFFF'><b>[ OFFICIAL SPEC SHEET ]</b></font><br/>"
        f"<font size='7' color='#71717A'><b>ISSUED: {date_str.upper()}</b></font>"
        f"</para>",
        ParagraphStyle("MetaHead", leading=10),
    )

    header_table = Table(
        [[header_logo_cells[0], brand_para, badge_meta_para]],
        colWidths=[34, page_width - 174, 140],
    )
    header_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), DARK_BG),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 14),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
            ("LEFTPADDING", (0, 0), (-1, -1), 16),
            ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ])
    )
    story.append(header_table)

    # ─────────────────────────────────────────────────────────────
    # 2. MERCHANT IDENTITY BAR
    # ─────────────────────────────────────────────────────────────
    buyer_name = (buyer_user.get("full_name") or "MERCHANT BUYER").upper()
    biz_legal = buyer_business.get("legal_name") or buyer_user.get("email") or "Wholesale Account"
    city = (buyer_business.get("city") or "KAMPALA").upper()
    address = buyer_business.get("physical_address") or "Headquarters / Central Depot"
    phone = buyer_business.get("phone_number") or buyer_user.get("phone_number") or "+256 700 000 000"

    merch_col1 = Paragraph(
        f"<font size='6' color='#71717A'><b>ISSUED TO MERCHANT</b></font><br/>"
        f"<font size='8.5' color='#1A1A2E'><b>{buyer_name}</b></font><br/>"
        f"<font size='6.5' color='#71717A'>{biz_legal}</font>",
        ParagraphStyle("Merch1", leading=9),
    )
    merch_col2 = Paragraph(
        f"<font size='6' color='#71717A'><b>LOGISTICS ORIGIN / DESTINATION</b></font><br/>"
        f"<font size='8.5' color='#1A1A2E'><b>{city}</b></font><br/>"
        f"<font size='6.5' color='#71717A'>{address}</font>",
        ParagraphStyle("Merch2", leading=9),
    )
    merch_col3 = Paragraph(
        f"<para align='right'>"
        f"<font size='6' color='#71717A'><b>CONTACT REGISTRY</b></font><br/>"
        f"<font size='8.5' color='#1A1A2E'><b>{phone}</b></font><br/>"
        f"<font size='6.5' color='#00BFFF'><b>NODE PARTNER NETWORK</b></font>"
        f"</para>",
        ParagraphStyle("Merch3", leading=9),
    )

    merchant_table = Table(
        [[merch_col1, merch_col2, merch_col3]],
        colWidths=[page_width * 0.36, page_width * 0.38, page_width * 0.26],
    )
    merchant_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ])
    )
    story.append(merchant_table)
    story.append(Spacer(1, 14))

    # ─────────────────────────────────────────────────────────────
    # 3. ENTRIES / LINE ITEMS BREAKDOWN
    # ─────────────────────────────────────────────────────────────
    entries = order.get("entries_json") or []
    if isinstance(entries, dict):
        entries = [entries]

    grand_total_calc = 0.0
    total_units_calc = 0

    for idx, entry in enumerate(entries):
        product = entry.get("savedProduct", {}).get("product") or entry.get("product") or {}
        prod_name = (product.get("name") or entry.get("name") or "Wholesale Item").upper()
        prod_brand = (product.get("brand") or "N.O.D.E.").upper()
        prod_sku = product.get("sku") or "SKU-N/A"
        total_units = int(entry.get("totalUnits") or entry.get("quantity") or 1)
        total_units_calc += total_units

        # Pricing calculation
        price_tiers = product.get("price_tiers") or []
        unit_price = float(product.get("srp") or 0.0)
        # Check tier
        for t in sorted(price_tiers, key=lambda x: x.get("minQuantity", 0), reverse=True):
            if total_units >= t.get("minQuantity", 0):
                unit_price = float(t.get("price", unit_price))
                break
        
        entry_total = unit_price * total_units
        grand_total_calc += entry_total

        # --- Product Dark Header Banner ---
        p_title_para = Paragraph(
            f"<font size='12' color='#FFFFFF'><b>{prod_name}</b></font><br/>"
            f"<font size='7' color='#00BFFF'><b>{prod_brand}</b></font> "
            f"<font size='7' color='#71717A'>| REFERENCE SKU: {prod_sku}</font>",
            ParagraphStyle("PTitle", leading=12),
        )
        p_units_badge = Paragraph(
            f"<para align='center'><font size='8' color='#FFFFFF'><b>{total_units} UNITS</b></font></para>",
            ParagraphStyle("PUnits"),
        )
        p_header_table = Table(
            [[p_title_para, p_units_badge]],
            colWidths=[page_width - 95, 95],
        )
        p_header_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), DARK_BG),
                ("BACKGROUND", (1, 0), (1, 0), PRIMARY_COLOR),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ])
        )
        story.append(p_header_table)

        # --- Product Specs Row (Supplier, Availability, Lead Time, Trading Terms) ---
        supplier_name = (
            supplier_business.get("legal_name")
            or product.get("supplier", {}).get("name")
            or order.get("business_name")
            or "Verified Supplier"
        ).upper()
        current_stock = product.get("current_stock", "In Stock")
        lead_time = product.get("lead_time_days", 3)
        trading_terms = (
            product.get("trading_terms", {}).get("content")
            or "Standard wholesale trading terms apply. Goods inspected upon delivery."
        )

        specs_left = Paragraph(
            f"<font size='6' color='#71717A'><b>SUPPLIER</b></font><br/>"
            f"<font size='8' color='#FFFFFF'><b>{supplier_name}</b></font><br/>"
            f"<font size='6.5' color='#A1A1AA'>{supplier_business.get('physical_address') or 'Certified Fulfillment Depot'}</font>",
            ParagraphStyle("SpecsLeft", leading=8.5),
        )

        stock_badge = Paragraph(
            f"<font size='6' color='#71717A'><b>AVAILABILITY</b></font><br/>"
            f"<font size='7.5' color='#22C55E'><b>{current_stock} UNITS</b></font>",
            ParagraphStyle("Stk", leading=8),
        )
        lead_badge = Paragraph(
            f"<font size='6' color='#71717A'><b>LEAD TIME</b></font><br/>"
            f"<font size='7.5' color='#00BFFF'><b>{lead_time} DAYS</b></font>",
            ParagraphStyle("Lead", leading=8),
        )
        terms_block = Paragraph(
            f"<font size='6' color='#71717A'><b>TRADING TERMS</b></font><br/>"
            f"<font size='6.5' color='#A1A1AA'>{trading_terms}</font>",
            ParagraphStyle("Terms", leading=8),
        )

        specs_table = Table(
            [[specs_left, stock_badge, lead_badge, terms_block]],
            colWidths=[page_width * 0.35, page_width * 0.18, page_width * 0.15, page_width * 0.32],
        )
        specs_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), DARK_BG),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("LINEBEFORE", (1, 0), (-1, -1), 0.5, BORDER_DARK),
            ])
        )
        story.append(specs_table)

        # --- Matrix Data Table (Colors x Sizes) ---
        groups = entry.get("confirmedGroups") or []
        all_sizes_set = set()
        for g in groups:
            for sz in (g.get("sizeQtys") or {}).keys():
                all_sizes_set.add(sz)
        size_list = sorted(list(all_sizes_set)) if all_sizes_set else ["STD"]

        # Build Table rows
        matrix_header = [
            Paragraph("<font size='7' color='#FFFFFF'><b>COLOR</b></font>", ParagraphStyle("HdrC")),
        ]
        for sz in size_list:
            matrix_header.append(
                Paragraph(f"<para align='center'><font size='7' color='#FFFFFF'><b>{sz.upper()}</b></font></para>", ParagraphStyle("HdrSz"))
            )
        matrix_header.append(
            Paragraph("<para align='center'><font size='7' color='#FFFFFF'><b>TOTAL</b></font></para>", ParagraphStyle("HdrT"))
        )

        matrix_rows = [matrix_header]
        if groups:
            for g in groups:
                c_label = g.get("color", {}).get("label") or "Standard"
                sq = g.get("sizeQtys") or {}
                row_total = sum(int(v or 0) for v in sq.values())

                row_cells = [
                    Paragraph(f"<font size='7.5' color='#1A1A2E'><b>{c_label}</b></font>", ParagraphStyle("ColorLbl")),
                ]
                for sz in size_list:
                    qty = sq.get(sz, 0)
                    qty_str = str(qty) if qty > 0 else "-"
                    q_color = "#1A1A2E" if qty > 0 else "#A1A1AA"
                    row_cells.append(
                        Paragraph(f"<para align='center'><font size='7.5' color='{q_color}'>{qty_str}</font></para>", ParagraphStyle("QC"))
                    )
                row_cells.append(
                    Paragraph(f"<para align='center'><font size='7.5' color='#1A1A2E'><b>{row_total}</b></font></para>", ParagraphStyle("RT"))
                )
                matrix_rows.append(row_cells)
        else:
            # Fallback simple row if groups wasn't populated
            row_cells = [
                Paragraph("<font size='7.5' color='#1A1A2E'><b>Default Specification</b></font>", ParagraphStyle("DefLbl")),
            ]
            for sz in size_list:
                row_cells.append(Paragraph(f"<para align='center'><font size='7.5' color='#1A1A2E'>{total_units}</font></para>", ParagraphStyle("DefQ")))
            row_cells.append(Paragraph(f"<para align='center'><font size='7.5' color='#1A1A2E'><b>{total_units}</b></font></para>", ParagraphStyle("DefTot")))
            matrix_rows.append(row_cells)

        num_cols = len(matrix_header)
        col_w_color = page_width * 0.40
        rem_w = page_width - col_w_color
        other_w = rem_w / (num_cols - 1)
        matrix_col_widths = [col_w_color] + [other_w] * (num_cols - 1)

        matrix_table = Table(matrix_rows, colWidths=matrix_col_widths)
        matrix_style = [
            ("BACKGROUND", (0, 0), (-1, 0), DARK_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]
        # Alternate background for rows
        for r_i in range(1, len(matrix_rows)):
            if r_i % 2 == 0:
                matrix_style.append(("BACKGROUND", (0, r_i), (-1, r_i), LIGHT_BG))
        matrix_table.setStyle(TableStyle(matrix_style))
        story.append(matrix_table)

        # --- Pricing Subtotal Row ---
        price_summary_para = Paragraph(
            f"<para align='right'>"
            f"<font size='6.5' color='#71717A'><b>UNIT PRICE (TIERED):</b></font> "
            f"<font size='7.5' color='#1A1A2E'><b>{_format_ugx(unit_price)}</b></font>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
            f"<font size='7.5' color='#1A1A2E'><b>TOTAL ITEM VALUE:</b></font> "
            f"<font size='10' color='#00BFFF'><b>{_format_ugx(entry_total)}</b></font>"
            f"</para>",
            ParagraphStyle("SubtotPara", leading=11),
        )
        price_subtable = Table([[price_summary_para]], colWidths=[page_width])
        price_subtable.setStyle(
            TableStyle([
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ])
        )
        story.append(price_subtable)

    # ─────────────────────────────────────────────────────────────
    # 4. ORDER CONSOLIDATION SUMMARY / GRAND TOTAL
    # ─────────────────────────────────────────────────────────────
    grand_total_display = float(order.get("total_amount") or grand_total_calc)
    consol_left = Paragraph(
        f"<font size='6.5' color='#71717A'><b>ORDER CONSOLIDATION SUMMARY</b></font><br/>"
        f"<font size='8' color='#18181B'><b>Total Consolidated Units: {total_units_calc}</b></font>",
        ParagraphStyle("ConsolLeft", leading=9),
    )
    consol_right = Paragraph(
        f"<para align='right'>"
        f"<font size='7' color='#18181B'><b>GRAND TOTAL VALUE</b></font><br/>"
        f"<font size='14' color='#00BFFF'><b>{_format_ugx(grand_total_display)}</b></font>"
        f"</para>",
        ParagraphStyle("ConsolRight", leading=13),
    )
    consol_table = Table([[consol_left, consol_right]], colWidths=[page_width * 0.5, page_width * 0.5])
    consol_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), CARD_FILL),
            ("LINEABOVE", (0, 0), (-1, -1), 2, PRIMARY_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ])
    )
    story.append(consol_table)
    story.append(Spacer(1, 14))

    # ─────────────────────────────────────────────────────────────
    # 5. DELIVERY VERIFICATION CLEARANCE (QR CODE BLOCK)
    # ─────────────────────────────────────────────────────────────
    qr_img = _make_qr_image(order_id, size=65)

    qr_desc_para = Paragraph(
        f"<font size='8' color='#00BFFF'><b>DELIVERY VERIFICATION CLEARANCE</b></font><br/>"
        f"<font size='6.5' color='#FFFFFF'>This document acts as the official ownership token for the wholesale items listed. "
        f"The recipient merchant must scan the QR code using the N.O.D.E. app upon receipt to verify and complete custody transfer.</font><br/><br/>"
        f"<font size='6.5' color='#A1A1AA'><b>TOKEN ID: {order_id}</b></font>",
        ParagraphStyle("QRDesc", leading=8.5),
    )

    qr_table = Table([[qr_img, qr_desc_para]], colWidths=[75, page_width - 75])
    qr_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), DARK_BG),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ])
    )
    story.append(qr_table)
    story.append(Spacer(1, 12))

    # ─────────────────────────────────────────────────────────────
    # 6. FOOTER
    # ─────────────────────────────────────────────────────────────
    footer_text = Paragraph(
        f"<font size='6.5' color='#71717A'>Generated by N.O.D.E. Wholesale Platform in association with NEXUS &bull; Official Digital B2B Clearance Record</font>",
        ParagraphStyle("FootText"),
    )
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER_COLOR, spaceAfter=4))
    story.append(footer_text)

    # Build the document
    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
