# -*- coding: utf-8 -*-
"""
email_alert.py — Sistema de alertas por email para scrapers fallidos.
Destinatario fijo: nicolas.crespo@southbay.com.ar
"""
from __future__ import annotations
import os
import smtplib
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger("alert")

ALERT_EMAIL = "nicolas.crespo@southbay.com.ar"
SMTP_HOST   = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER   = os.getenv("SMTP_USER", "")
SMTP_PASS   = os.getenv("SMTP_PASS", "")


def send_scraper_alert(
    scraper_name: str,
    error_msg:    str,
    severity:     str = "critical",
    extra_ctx:    dict | None = None,
):
    """Envía alerta de fallo de scraper por email."""
    subject = f"[Nike Dashboard] {'🔴 CRÍTICO' if severity=='critical' else '⚠️ WARNING'} — {scraper_name} falló"

    ctx_rows = ""
    if extra_ctx:
        ctx_rows = "".join(
            f"<tr><td style='padding:4px 8px;color:#666;font-size:12px'>{k}</td>"
            f"<td style='padding:4px 8px;font-size:12px'><strong>{v}</strong></td></tr>"
            for k, v in extra_ctx.items()
        )

    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:600px;margin:0 auto;border:1px solid #eee;border-radius:12px;overflow:hidden">
      <div style="background:#111111;padding:24px 28px">
        <div style="display:flex;align-items:center;gap:12px">
          <svg width="40" height="15" viewBox="0 0 80 30" fill="none">
            <path d="M8.027 22.911L24.234 3.493c.734-.874 1.836-1.38 3.009-1.38h42.49c.617 0 1.02.662.734 1.209L54.65 22.204c-.734 1.38-2.139 2.254-3.67 2.254H8.551c-.794 0-1.256-.92-.524-1.547z" fill="#E31837"/>
          </svg>
          <span style="color:white;font-weight:700;font-size:14px;letter-spacing:2px">ANALYTICS DASHBOARD</span>
        </div>
      </div>
      <div style="background:#fff;padding:28px">
        <div style="background:{'#fff5f5' if severity=='critical' else '#fffbf0'};border:1px solid {'#fecaca' if severity=='critical' else '#fde68a'};border-radius:8px;padding:16px;margin-bottom:20px">
          <h2 style="margin:0 0 6px 0;color:{'#dc2626' if severity=='critical' else '#d97706'};font-size:16px">
            {'🔴 Scraper Crítico Falló' if severity=='critical' else '⚠️ Warning de Scraper'}
          </h2>
          <p style="margin:0;color:#374151;font-size:14px"><strong>{scraper_name}</strong> reportó un error.</p>
        </div>

        <table style="width:100%;border-collapse:collapse;margin-bottom:20px;background:#f9fafb;border-radius:8px;overflow:hidden">
          <tr><td style="padding:4px 8px;color:#666;font-size:12px">Scraper</td><td style="padding:4px 8px;font-size:12px"><strong>{scraper_name}</strong></td></tr>
          <tr><td style="padding:4px 8px;color:#666;font-size:12px">Severidad</td><td style="padding:4px 8px;font-size:12px"><strong style="color:{'#dc2626' if severity=='critical' else '#d97706'}">{severity.upper()}</strong></td></tr>
          <tr><td style="padding:4px 8px;color:#666;font-size:12px">Timestamp</td><td style="padding:4px 8px;font-size:12px">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
          {ctx_rows}
        </table>

        <div style="margin-bottom:20px">
          <p style="color:#374151;font-size:13px;font-weight:600;margin:0 0 8px 0">Error:</p>
          <pre style="background:#111;color:#f87171;padding:16px;border-radius:8px;font-size:11px;overflow-x:auto;white-space:pre-wrap">{error_msg}</pre>
        </div>

        <div style="background:#f3f4f6;border-radius:8px;padding:16px">
          <p style="margin:0;color:#6b7280;font-size:12px">
            Este email fue generado automáticamente por Nike Analytics Dashboard · Southbay<br>
            Para configurar alertas: editar <code>scraper/alerts/email_alert.py</code>
          </p>
        </div>
      </div>
    </div>
    """

    if not SMTP_USER or not SMTP_PASS:
        log.warning("SMTP no configurado. Alerta no enviada: %s", subject)
        log.error("ALERT: %s\n%s", subject, error_msg)
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = SMTP_USER
        msg["To"]      = ALERT_EMAIL
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)

        log.info("📧 Alerta enviada a %s", ALERT_EMAIL)
        return True
    except Exception as e:
        log.error("Error enviando alerta: %s", e)
        return False


def send_run_summary(results: list[dict]):
    """Envía resumen de la corrida semanal."""
    success = [r for r in results if r.get("success")]
    failed  = [r for r in results if not r.get("success")]

    rows = ""
    for r in results:
        icon  = "✅" if r.get("success") else "❌"
        color = "#22c55e" if r.get("success") else "#ef4444"
        rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #f3f4f6">{icon} {r.get('scraper','?')}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #f3f4f6;color:{color}">
            {'OK' if r.get('success') else r.get('error','Error')[:60]}
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #f3f4f6;color:#9ca3af">
            {r.get('duration_s', 0):.0f}s · {r.get('rows', 0):,} filas
          </td>
        </tr>"""

    subject = f"[Nike Dashboard] Resumen corrida semanal — {len(success)}/{len(results)} OK"
    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:600px;margin:0 auto">
      <div style="background:#111;padding:20px 24px">
        <span style="color:white;font-weight:700;font-size:14px;letter-spacing:2px">NIKE DASHBOARD — RESUMEN SEMANAL</span>
      </div>
      <div style="padding:24px">
        <div style="display:flex;gap:16px;margin-bottom:20px">
          <div style="flex:1;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:16px;text-align:center">
            <div style="font-size:28px;font-weight:700;color:#16a34a">{len(success)}</div>
            <div style="font-size:12px;color:#166534">Exitosos</div>
          </div>
          <div style="flex:1;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:16px;text-align:center">
            <div style="font-size:28px;font-weight:700;color:#dc2626">{len(failed)}</div>
            <div style="font-size:12px;color:#991b1b">Fallidos</div>
          </div>
        </div>
        <table style="width:100%;border-collapse:collapse">
          <thead><tr style="background:#111">
            <th style="padding:8px 12px;color:white;text-align:left;font-size:11px">Scraper</th>
            <th style="padding:8px 12px;color:white;text-align:left;font-size:11px">Estado</th>
            <th style="padding:8px 12px;color:white;text-align:left;font-size:11px">Duración / Filas</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
        <p style="color:#9ca3af;font-size:11px;margin-top:20px">{datetime.now().strftime('%Y-%m-%d %H:%M')} · Nike Dashboard · Southbay</p>
      </div>
    </div>"""

    if SMTP_USER and SMTP_PASS:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = SMTP_USER
            msg["To"]      = ALERT_EMAIL
            msg.attach(MIMEText(html, "html"))
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
            log.info("📧 Resumen enviado a %s", ALERT_EMAIL)
        except Exception as e:
            log.error("Error enviando resumen: %s", e)
