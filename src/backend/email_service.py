import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any

logger = logging.getLogger(__name__)

DEFAULT_SENDER = os.getenv("NEXUS_EMAIL_SENDER", "nexus@krusch.dev")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "nexus@krusch.dev")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

def render_verification_email_html(username: str, verification_token: str, verification_link: str) -> str:
    """Renders a modern, dark-mode glassmorphic HTML verification email for Krusch-Nexus."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Verify your Krusch-Nexus Email</title>
    <style>
        body {{
            margin: 0;
            padding: 0;
            background-color: #0b0f19;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            color: #f3f4f6;
            -webkit-font-smoothing: antialiased;
        }}
        .email-wrapper {{
            width: 100%;
            background-color: #0b0f19;
            padding: 40px 0;
        }}
        .email-container {{
            max-width: 600px;
            margin: 0 auto;
            background: #111827;
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
        }}
        .email-header {{
            padding: 32px 40px 24px;
            background: linear-gradient(180deg, rgba(59, 130, 246, 0.15) 0%, rgba(17, 24, 39, 0) 100%);
            text-align: center;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }}
        .brand-title {{
            font-size: 24px;
            font-weight: 700;
            color: #ffffff;
            letter-spacing: -0.5px;
            margin: 0;
        }}
        .brand-badge {{
            display: inline-block;
            margin-top: 8px;
            padding: 4px 12px;
            background: rgba(59, 130, 246, 0.2);
            border: 1px solid rgba(59, 130, 246, 0.4);
            border-radius: 20px;
            color: #60a5fa;
            font-size: 12px;
            font-weight: 600;
        }}
        .email-body {{
            padding: 40px;
        }}
        .greeting {{
            font-size: 20px;
            font-weight: 600;
            color: #ffffff;
            margin-top: 0;
            margin-bottom: 16px;
        }}
        .lead-text {{
            font-size: 15px;
            color: #9ca3af;
            line-height: 1.6;
            margin-bottom: 32px;
        }}
        .cta-wrapper {{
            text-align: center;
            margin: 36px 0;
        }}
        .cta-button {{
            display: inline-block;
            padding: 16px 36px;
            background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
            color: #ffffff !important;
            text-decoration: none;
            font-weight: 600;
            font-size: 16px;
            border-radius: 8px;
            box-shadow: 0 4px 14px rgba(59, 130, 246, 0.4);
            transition: all 0.2s ease;
        }}
        .link-fallback {{
            margin-top: 32px;
            padding: 16px;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px;
            word-break: break-all;
            font-size: 13px;
            color: #6b7280;
        }}
        .link-url {{
            color: #60a5fa;
            text-decoration: none;
        }}
        .feature-box {{
            margin-top: 32px;
            padding: 20px;
            background: rgba(17, 24, 39, 0.6);
            border-left: 3px solid #3b82f6;
            border-radius: 0 8px 8px 0;
        }}
        .feature-item {{
            font-size: 13px;
            color: #d1d5db;
            margin-bottom: 8px;
        }}
        .feature-item:last-child {{
            margin-bottom: 0;
        }}
        .email-footer {{
            padding: 24px 40px;
            background: #0d131f;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            text-align: center;
            font-size: 12px;
            color: #6b7280;
        }}
    </style>
</head>
<body>
    <div class="email-wrapper">
        <div class="email-container">
            <div class="email-header">
                <h1 class="brand-title">Krusch-Nexus</h1>
                <span class="brand-badge">Open Beta Access</span>
            </div>
            <div class="email-body">
                <h2 class="greeting">Welcome aboard, {username}!</h2>
                <p class="lead-text">
                    Thank you for signing up for the Krusch-Nexus beta trial. Please confirm your email address to verify your account and instantly activate your full Pro Tier monthly query quota.
                </p>
                <div class="cta-wrapper">
                    <a href="{verification_link}" class="cta-button" target="_blank">Verify Email Address</a>
                </div>
                <div class="feature-box">
                    <div class="feature-item">⚡ <strong>25,000 Monthly Queries</strong>: Unlocked upon email verification</div>
                    <div class="feature-item">🧠 <strong>Dense Vector & GraphRAG</strong>: 1024d BGE-large embeddings + entity linking</div>
                    <div class="feature-item">🛡️ <strong>Air-Gapped Privacy</strong>: Local-first security for institutional knowledge</div>
                </div>
                <div class="link-fallback">
                    <p style="margin: 0 0 8px 0;">If the button above does not work, copy and paste this link into your browser:</p>
                    <a href="{verification_link}" class="link-url">{verification_link}</a>
                </div>
            </div>
            <div class="email-footer">
                <p style="margin: 0 0 6px 0;">Sent by <strong>Krusch-Nexus Platform</strong> &lt;{DEFAULT_SENDER}&gt;</p>
                <p style="margin: 0;">© 2026 Krusch-Nexus Inc. · Local Institutional Knowledge Engine</p>
            </div>
        </div>
    </div>
</body>
</html>"""

def send_verification_email(recipient_email: str, verification_token: str, base_url: str = "https://krusch.dev") -> Dict[str, Any]:
    """
    Sends or logs a verification email from nexus@krusch.dev.
    Uses SMTP if credentials are configured, or logs formatted dispatch in local/dev mode.
    """
    verification_link = f"{base_url.rstrip('/')}/api/verify-email?token={verification_token}"
    html_content = render_verification_email_html(recipient_email, verification_token, verification_link)

    sender_email = DEFAULT_SENDER
    subject = "Verify your email for Krusch-Nexus Open Beta"

    dispatched = False
    if SMTP_PASSWORD:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"Krusch-Nexus <{sender_email}>"
            msg["To"] = recipient_email

            part_html = MIMEText(html_content, "html")
            msg.attach(part_html)

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)

            dispatched = True
            logger.info(f"Successfully dispatched verification email from {sender_email} to {recipient_email}")
        except Exception as e:
            logger.error(f"Failed to send email via SMTP ({e}). Falling back to telemetry logging.")

    if not dispatched:
        logger.info(f"[DEV EMAIL DISPATCH] From: {sender_email} | To: {recipient_email} | Verification Link: {verification_link}")

    return {
        "status": "dispatched" if dispatched else "logged_dev_mode",
        "sender": sender_email,
        "recipient": recipient_email,
        "verification_link": verification_link
    }
