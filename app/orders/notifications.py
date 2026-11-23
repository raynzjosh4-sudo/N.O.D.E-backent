"""
Background task worker for order status notifications, emails, and spec sheet attachments.
"""
import logging
from app.profile.supabase_client import fetch_business, fetch_user
from app.services.email_service import (
    build_order_status_email_html,
    create_in_app_notification,
    send_order_email,
)
from app.services.pdf_generator import _format_ugx, generate_order_pdf

logger = logging.getLogger(__name__)


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
    4. Sends confirmation / new order email + PDF attachment to the supplier.
    5. Writes in-app notifications to notifications_table for both parties.
    """
    order_id = str(order.get("order_id") or order.get("id"))
    buyer_id = order.get("user_id")
    supplier_id = order.get("supplier_id")

    # If supplier_id not at root level, extract from order entries
    if not supplier_id:
        entries = order.get("entries_json") or order.get("entries") or []
        for e in entries:
            if isinstance(e, dict):
                s = (
                    e.get("supplier_id")
                    or e.get("supplierId")
                    or (e.get("product_snapshot") or {}).get("supplier_id")
                    or (e.get("product") or {}).get("supplierId")
                )
                if s:
                    supplier_id = s
                    break

    total_amount = float(order.get("total_amount") or 0.0)
    total_ugx = _format_ugx(total_amount)

    buyer_user = (await fetch_user(buyer_id)) if buyer_id else {}
    buyer_biz = (await fetch_business(buyer_id)) if buyer_id else {}
    supplier_user = (await fetch_user(supplier_id)) if supplier_id else {}
    supplier_biz = (await fetch_business(supplier_id)) if supplier_id else {}

    # Status formatting
    status_lower = (new_status or "new").lower()
    status_upper = status_lower.upper()
    is_new_order = status_lower in ("new", "submitted", "pending")

    color_map = {
        "NEW": "#10B981",
        "PROCESSING": "#3B82F6",
        "CONFIRMED": "#22C55E",
        "COMPLETED": "#22C55E",
        "CANCELLED": "#EF4444",
    }
    status_color = color_map.get(status_upper, "#10B981")

    buyer_name = (
        order.get("business_name")
        or (buyer_biz or {}).get("legal_name")
        or (buyer_user or {}).get("full_name")
        or "Valued Buyer"
    )
    buyer_email = (buyer_user or {}).get("email") or order.get("buyer_email")
    buyer_phone = (
        order.get("business_phone")
        or (buyer_biz or {}).get("phone_number")
        or (buyer_user or {}).get("phone")
        or "N/A"
    )

    # Fallback to local DB if buyer email is not in Supabase
    if not buyer_email and buyer_id:
        try:
            from app.db.session import SessionLocal
            from app.db.models import User
            with SessionLocal() as db:
                local_b = db.query(User).filter(User.id == buyer_id).first()
                if local_b and local_b.email:
                    buyer_email = local_b.email
        except Exception as e:
            logger.debug(f"Could not resolve local buyer email: {e}")

    supplier_name = (
        (supplier_biz or {}).get("legal_name")
        or (supplier_user or {}).get("full_name")
        or order.get("supplier_name")
        or "Verified Supplier"
    )
    supplier_email = (
        (supplier_user or {}).get("email")
        or (supplier_biz or {}).get("email")
        or (supplier_biz or {}).get("contact_email")
        or order.get("supplier_email")
    )

    # Fallback to local DB if supplier email is not in Supabase
    if not supplier_email and supplier_id:
        try:
            from app.db.session import SessionLocal
            from app.db.models import User
            with SessionLocal() as db:
                local_s = db.query(User).filter(User.id == supplier_id).first()
                if local_s and local_s.email:
                    supplier_email = local_s.email
        except Exception as e:
            logger.debug(f"Could not resolve local supplier email: {e}")

    # 1. Generate Order PDF Spec Sheet
    try:
        pdf_bytes = generate_order_pdf(
            order=order,
            buyer_user=buyer_user,
            buyer_business=buyer_biz,
            supplier_user=supplier_user,
            supplier_business=supplier_biz,
        )
    except Exception as e:
        logger.error(f"Error generating PDF in background: {e}")
        pdf_bytes = None

    pdf_filename = f"NODE_Order_{order_id}.pdf"

    # 2. Send Email to the Supplier
    if supplier_email:
        if is_new_order:
            supplier_subject = f"📦 New Wholesale Order #{order_id} Received! ({total_ugx})"
            supplier_title = f"New Wholesale Order #{order_id} Received"
            supplier_message = (
                f"Great news! A wholesale buyer ({buyer_name}, email: {buyer_email or 'N/A'}, phone: {buyer_phone}) "
                f"has placed a new wholesale order #{order_id} with your business totaling {total_ugx}. "
                f"Please review the attached official Order Specification Sheet & Invoice PDF for the size/color breakdown, "
                f"verify inventory in your warehouse, and prepare for dispatch."
            )
        else:
            supplier_subject = f"Order #{order_id} Status Updated: {status_upper}"
            supplier_title = f"Order #{order_id} Status: {status_upper}"
            supplier_message = f"Order #[{order_id}] status has transitioned to '{status_upper}'."

        supplier_html = build_order_status_email_html(
            recipient_name=supplier_name,
            order_id=order_id,
            status_title=supplier_title,
            status_color=status_color,
            message=supplier_message,
            order_total_ugx=total_ugx,
            is_supplier=True,
        )

        logger.info(f"Sending order notification email to supplier {supplier_email} for order #{order_id}...")
        await send_order_email(
            recipient=supplier_email,
            subject=supplier_subject,
            html_content=supplier_html,
            pdf_bytes=pdf_bytes,
            pdf_filename=pdf_filename,
        )
    else:
        logger.warning(f"Could not send email to supplier for order #{order_id}: supplier_email is missing (supplier_id={supplier_id})")

    # In-app notification to supplier
    if supplier_id:
        notif_title = f"New Order #{order_id} Received!" if is_new_order else f"Order #{order_id} Status: {status_upper}"
        notif_body = (
            f"{buyer_name} placed a wholesale order totaling {total_ugx}."
            if is_new_order
            else f"Order #[{order_id}] status transitioned to {status_upper}."
        )
        await create_in_app_notification(
            user_id=supplier_id,
            title=notif_title,
            body=notif_body,
            category="order",
            metadata={"order_id": order_id, "status": new_status},
        )

    # 3. Send Email to the Buyer
    if buyer_email:
        if is_new_order:
            buyer_subject = f"N.O.D.E. Order Confirmation: #{order_id} ({total_ugx})"
            buyer_title = f"Order #{order_id} Placed Successfully"
            buyer_message = (
                f"Your wholesale order #[{order_id}] totaling {total_ugx} has been successfully submitted to {supplier_name}. "
                f"Attached is your official Order Specification Sheet & Pro-Forma Invoice for your records and warehouse clearance."
            )
        else:
            buyer_subject = f"N.O.D.E. Order #{order_id} Status: {status_upper}"
            buyer_title = f"Order Status: {status_upper}"
            buyer_message = f"Your wholesale order #[{order_id}] has been updated to '{status_upper}' by {supplier_name}."

        buyer_html = build_order_status_email_html(
            recipient_name=buyer_name,
            order_id=order_id,
            status_title=buyer_title,
            status_color=status_color,
            message=buyer_message,
            order_total_ugx=total_ugx,
            is_supplier=False,
        )

        logger.info(f"Sending order confirmation email to buyer {buyer_email} for order #{order_id}...")
        await send_order_email(
            recipient=buyer_email,
            subject=buyer_subject,
            html_content=buyer_html,
            pdf_bytes=pdf_bytes,
            pdf_filename=pdf_filename,
        )

    # In-app notification to buyer
    if buyer_id:
        buyer_notif_title = f"Order #{order_id} Placed" if is_new_order else f"Order #{order_id} is now {status_upper}"
        buyer_notif_body = (
            f"Your order totaling {total_ugx} has been sent to {supplier_name}."
            if is_new_order
            else f"Supplier {supplier_name} updated order status to {status_upper}."
        )
        await create_in_app_notification(
            user_id=buyer_id,
            title=buyer_notif_title,
            body=buyer_notif_body,
            category="order",
            metadata={"order_id": order_id, "status": new_status},
        )

    # 4. Link supplier with buyer who placed the order for future product broadcast updates
    if supplier_id and buyer_email:
        try:
            from app.db.session import SessionLocal
            from app.db.models import SupplierCustomer
            with SessionLocal() as db_session:
                existing_link = (
                    db_session.query(SupplierCustomer)
                    .filter(
                        SupplierCustomer.supplier_id == str(supplier_id),
                        SupplierCustomer.buyer_email == str(buyer_email).strip().lower(),
                    )
                    .first()
                )
                if existing_link:
                    existing_link.last_order_id = str(order_id)
                    if buyer_name and not existing_link.buyer_name:
                        existing_link.buyer_name = buyer_name
                    db_session.commit()
                else:
                    new_link = SupplierCustomer(
                        supplier_id=str(supplier_id),
                        buyer_user_id=str(buyer_id or action_by_user_id or ""),
                        buyer_email=str(buyer_email).strip().lower(),
                        buyer_name=buyer_name,
                        last_order_id=str(order_id),
                    )
                    db_session.add(new_link)
                    db_session.commit()
            logger.info(f"Recorded buyer-supplier link: {buyer_email} -> {supplier_id}")
        except Exception as link_err:
            logger.warning(f"Failed to record buyer-supplier customer link: {link_err}")


