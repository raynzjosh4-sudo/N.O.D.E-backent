"""
Unified email delivery service for N.O.D.E. Wholesale Platform.
Supports HTML emails and file attachments (e.g. Order PDF) via Resend API.
"""
import base64
import logging
from typing import Any, Optional
import httpx

from app.core.config import is_production, resend_api_key, resend_from_email
from app.notifications.supabase_client import register_device_token, _url, HEADERS

logger = logging.getLogger(__name__)
RESEND_API_URL = "https://api.resend.com/emails"


async def send_order_email(
    *,
    recipient: str,
    subject: str,
    html_content: str,
    pdf_bytes: Optional[bytes] = None,
    pdf_filename: str = "Order_Document.pdf",
) -> bool:
    """
    Send an order notification email, optionally with an attached PDF file.
    """
    api_key = resend_api_key()
    from_email = resend_from_email()
    sender = f"N.O.D.E. Wholesale <{from_email}>" if "<" not in from_email else from_email

    payload: dict[str, Any] = {
        "from": sender,
        "to": [recipient],
        "subject": subject,
        "html": html_content,
    }

    if pdf_bytes:
        encoded_content = base64.b64encode(pdf_bytes).decode("utf-8")
        payload["attachments"] = [
            {
                "filename": pdf_filename,
                "content": encoded_content,
            }
        ]

    if not api_key:
        print("\n=======================================================")
        print(f"📧 [DEV EMAIL SIMULATION - RESEND KEY NOT SET]")
        print(f"To: {recipient}")
        print(f"Subject: {subject}")
        print(f"Has PDF Attachment: {'Yes (' + str(len(pdf_bytes)) + ' bytes)' if pdf_bytes else 'No'}")
        print("=======================================================\n")
        return True

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(RESEND_API_URL, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                logger.info(f"Order email sent successfully to {recipient}")
                return True
            else:
                logger.error(f"Resend error ({resp.status_code}): {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Failed to deliver order email to {recipient}: {e}")
        return False


async def create_in_app_notification(
    user_id: str,
    title: str,
    body: str,
    category: str = "order",
    metadata: Optional[dict[str, Any]] = None,
) -> bool:
    """
    Write a real-time notification into Supabase notifications_table.
    """
    payload = {
        "user_id": user_id,
        "title": title,
        "body": body,
        "category": category,
        "is_unread": True,
        "metadata": metadata or {},
    }
    headers = {**HEADERS, "Prefer": "return=representation"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(_url("notifications_table"), json=payload, headers=headers)
            return resp.status_code in (200, 201)
    except Exception as e:
        logger.error(f"Failed to insert in-app notification: {e}")
        return False


def build_order_status_email_html(
    *,
    recipient_name: str,
    order_id: str,
    status_title: str,
    status_color: str,
    message: str,
    order_total_ugx: str,
    is_supplier: bool = False,
) -> str:
    """
    Responsive HTML email template styled with N.O.D.E. executive branding.
    """
    role_sub = "Supplier Copy" if is_supplier else "Merchant Copy"
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>N.O.D.E. Wholesale Order Update</title>
</head>
<body style="margin: 0; padding: 0; background-color: #0D0D1A; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #FFFFFF;">
  <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #0D0D1A; padding: 40px 10px;">
    <tr>
      <td align="center">
        <table width="600" border="0" cellspacing="0" cellpadding="0" style="max-width: 600px; width: 100%; background-color: #141424; border-radius: 8px; border: 1px solid #27273A; overflow: hidden;">
          
          <!-- BRAND HEADER -->
          <tr>
            <td style="background-color: #0B0B14; padding: 25px 30px; border-bottom: 2px solid #00BFFF;">
              <table width="100%" border="0" cellspacing="0" cellpadding="0">
                <tr>
                  <td>
                    <span style="font-size: 22px; font-weight: 800; letter-spacing: 5px; color: #FFFFFF;">N.O.D.E.</span><br/>
                    <span style="font-size: 8px; font-weight: 700; letter-spacing: 1.5px; color: #71717A;">NATIONAL ORDER &amp; DISTRIBUTION EXCHANGE</span>
                  </td>
                  <td align="right">
                    <span style="display: inline-block; padding: 4px 10px; border: 1px solid #00BFFF; border-radius: 2px; font-size: 9px; font-weight: 700; color: #00BFFF; letter-spacing: 1px;">{role_sub.upper()}</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- BODY -->
          <tr>
            <td style="padding: 35px 30px;">
              <p style="margin: 0 0 15px 0; font-size: 14px; color: #A1A1AA;">Hello <strong style="color: #FFFFFF;">{recipient_name}</strong>,</p>
              
              <div style="background-color: #1A1A2E; border-left: 4px solid {status_color}; padding: 18px 20px; border-radius: 4px; margin-bottom: 25px;">
                <span style="font-size: 11px; font-weight: 700; color: #71717A; text-transform: uppercase; letter-spacing: 1px;">ORDER STATUS UPDATE</span>
                <h2 style="margin: 6px 0 0 0; font-size: 18px; color: {status_color}; font-weight: 700;">{status_title}</h2>
                <p style="margin: 8px 0 0 0; font-size: 13px; color: #D4D4D8; line-height: 1.5;">{message}</p>
              </div>

              <!-- ORDER SUMMARY CARD -->
              <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #10101C; border: 1px solid #27273A; border-radius: 6px; margin-bottom: 25px;">
                <tr>
                  <td style="padding: 16px 20px; border-bottom: 1px solid #27273A;">
                    <span style="font-size: 11px; color: #71717A;">TOKEN / ORDER ID:</span><br/>
                    <strong style="font-size: 13px; color: #00BFFF; font-family: monospace;">{order_id}</strong>
                  </td>
                  <td style="padding: 16px 20px; border-bottom: 1px solid #27273A;" align="right">
                    <span style="font-size: 11px; color: #71717A;">ORDER VALUE:</span><br/>
                    <strong style="font-size: 15px; color: #FFFFFF;">{order_total_ugx}</strong>
                  </td>
                </tr>
                <tr>
                  <td colspan="2" style="padding: 14px 20px;">
                    <span style="font-size: 12px; color: #A1A1AA;">📎 The official <strong>N.O.D.E. Wholesale Spec Sheet &amp; Invoice PDF</strong> is attached to this email with QR clearance code for physical delivery.</span>
                  </td>
                </tr>
              </table>

              <p style="margin: 0; font-size: 12px; color: #71717A; line-height: 1.5;">
                You can also view this order and verify delivery anytime directly in the N.O.D.E. application.
              </p>
            </td>
          </tr>

          <!-- FOOTER -->
          <tr>
            <td style="background-color: #0B0B14; padding: 20px 30px; text-align: center; border-top: 1px solid #27273A;">
              <p style="margin: 0; font-size: 11px; color: #71717A;">
                N.O.D.E. Wholesale Platform &bull; Automated System Dispatch &bull; in association with NEXUS
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
