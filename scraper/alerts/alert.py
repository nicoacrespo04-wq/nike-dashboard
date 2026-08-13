"""
Nike Dashboard — alert.py

Sistema de alertas por email para los scrapers del dashboard de Nike Argentina.

Cuando un scraper falla (bloqueo del sitio, cambio de estructura HTML, timeout,
etc.) este módulo envía un email de aviso a nicolas.crespo@southbay.com.ar sin
falta. También permite mandar un resumen liviano cuando una corrida termina bien,
para tener visibilidad aunque no haya pasado nada malo.

Reusa el mismo patrón/proveedor que el proyecto SCOUT (repo de referencia en
C:\\Users\\josef\\OneDrive\\Desktop\\AWS\\Scout\\scraper\\tasks\\send_compliance_alert.py
y adapter_health_monitor.py): Resend vía su API HTTP, usando sólo `urllib.request`
(sin dependencias extra) y la API key leída de una variable de entorno.

Variables de entorno usadas:
  RESEND_API_KEY   — API key de Resend (obligatoria para poder enviar de verdad).
  EMAIL_FROM       — Remitente verificado en Resend. Si no está seteado o es un
                      dominio genérico (gmail.com, hotmail.com, etc.) se usa el
                      remitente de pruebas "onboarding@resend.dev" (igual que en
                      Scout), útil mientras no hay un dominio propio verificado.
  EMAIL_TO         — Destinatario de las alertas. Por defecto
                      "nicolas.crespo@southbay.com.ar" (pedido explícito del
                      negocio: avisar sin falta a esa dirección).

Uso típico dentro de un scraper:

    from scraper.alerts.alert import send_scraper_failure_alert

    try:
        productos = correr_scraper()
    except Exception as e:
        send_scraper_failure_alert(
            scraper_name="nike-ar-running",
            error=str(e),
            context={"productos_procesados": 137, "pagina": 5},
        )
        raise

Prueba manual:
    python scraper/alerts/alert.py --test
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("nike_dashboard.alerts")

# ─────────────────────────────────────────────────────────────────────────────
#  Configuración (leída de variables de entorno — nunca hardcodear la API key)
# ─────────────────────────────────────────────────────────────────────────────

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "onboarding@resend.dev")
EMAIL_TO = os.getenv("EMAIL_TO", "nicolas.crespo@southbay.com.ar")

RESEND_ENDPOINT = "https://api.resend.com/emails"

# Fallback local: si el email no se puede enviar por lo que sea, la alerta se
# escribe acá para no perderla en silencio.
FAILED_ALERTS_LOG = Path(__file__).resolve().parent / "failed_alerts.log"

_GENERIC_DOMAINS = ("gmail.com", "hotmail.com", "yahoo.com", "outlook.com")


def _resolve_sender() -> str:
    """
    Devuelve el remitente a usar en el email.

    Si EMAIL_FROM no está seteado, o es un dominio genérico (gmail, hotmail,
    etc. — que Resend no deja usar como remitente), cae al remitente de
    pruebas de Resend "onboarding@resend.dev", igual que hace Scout.
    """
    sender = (EMAIL_FROM or "").strip()
    if not sender or sender.lower().split("@")[-1] in _GENERIC_DOMAINS:
        return "onboarding@resend.dev"
    return sender


def _write_failed_alert_log(reason: str, payload: dict) -> None:
    """
    Escribe en `failed_alerts.log` cuando el envío por Resend falla, para que
    la notificación no se pierda silenciosamente aunque el email no haya salido.

    Nunca debe lanzar una excepción hacia el llamador: si ni siquiera se puede
    escribir el log local, sólo se deja constancia por stderr/logger.
    """
    entry = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "payload": payload,
    }
    try:
        FAILED_ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(FAILED_ALERTS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        logger.error("Alerta no enviada por email — guardada en %s (%s)", FAILED_ALERTS_LOG, reason)
    except Exception as log_error:
        # Última línea de defensa: si ni el log local funciona, al menos que
        # quede algo en la salida estándar de error.
        logger.critical(
            "No se pudo enviar el email NI escribir el log de fallback (%s). "
            "Alerta perdida: %s",
            log_error, entry,
        )


def _send_email_via_resend(subject: str, html_body: str, to_email: str) -> dict:
    """
    Envía un email usando la API HTTP de Resend (mismo proveedor que SCOUT),
    sin dependencias extra — sólo `urllib.request` de la stdlib.

    Devuelve un dict con {"sent": bool, ...}. Nunca lanza excepciones: cualquier
    error de red, de la API, o de configuración se captura y se reporta en el
    resultado.
    """
    if not RESEND_API_KEY:
        return {"sent": False, "error": "RESEND_API_KEY no configurada"}

    payload = json.dumps({
        "from": f"Nike Dashboard Alerts <{_resolve_sender()}>",
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }).encode("utf-8")

    req = urllib.request.Request(
        RESEND_ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = json.loads(resp.read())
        return {"sent": True, "email_id": resp_data.get("id")}
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(e)
        return {"sent": False, "error": f"HTTP {e.code}: {detail}"}
    except Exception as e:
        return {"sent": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  Alerta de fallo de scraper (garantizada — con fallback a log local)
# ─────────────────────────────────────────────────────────────────────────────

def send_scraper_failure_alert(scraper_name: str, error: str, context: dict | None = None) -> dict:
    """
    Arma y envía un email de alerta cuando un scraper falla.

    El email incluye: qué scraper falló, el error puntual, el timestamp (UTC y
    hora local del server) y cualquier contexto extra que se le pase (por ej.
    cuántos productos se llegaron a procesar antes de fallar, la URL que estaba
    scrapeando, el número de página, etc.).

    Este envío está pensado para ser "garantizado": si el email no se puede
    mandar por lo que sea (falta la API key, Resend está caído, error de red),
    la alerta se escribe igual en `scraper/alerts/failed_alerts.log` para no
    perder la notificación en silencio. Esta función no lanza excepciones.

    Args:
        scraper_name: Nombre identificador del scraper que falló
                      (ej. "nike-ar-running", "nike-ar-training").
        error: Descripción puntual del error (mensaje de excepción, motivo del
               bloqueo, timeout, etc.).
        context: Dict opcional con información adicional relevante para
                 diagnosticar el fallo (ej. {"productos_procesados": 137,
                 "pagina": 5, "url": "..."}).

    Returns:
        Dict con el resultado del envío: {"sent": bool, "email_id"/"error": ...}.
    """
    context = context or {}
    now_utc = datetime.now(timezone.utc)

    subject = f"[Nike Dashboard] Scraper falló: {scraper_name}"

    context_rows = "".join(
        f"<tr>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;color:#6b7280'>{k}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb'>{v}</td>"
        f"</tr>"
        for k, v in context.items()
    ) or "<tr><td colspan='2' style='padding:6px 10px;color:#9ca3af'>(sin contexto adicional)</td></tr>"

    html_body = f"""
    <html><body style='font-family:Arial,sans-serif;color:#111;max-width:700px;margin:0 auto'>
      <div style='background:#7f1d1d;color:#fff;padding:16px 20px;border-radius:10px 10px 0 0'>
        <h1 style='margin:0;font-size:20px'>Scraper falló — {scraper_name}</h1>
        <div style='opacity:0.8;font-size:13px;margin-top:4px'>
          Nike Dashboard · {now_utc.strftime('%d/%m/%Y %H:%M:%S UTC')}
        </div>
      </div>

      <div style='padding:12px 18px;background:#fef2f2;border:1px solid #fecaca;border-top:none'>
        <p style='margin:0'><b>Error:</b></p>
        <pre style='white-space:pre-wrap;background:#fff;border:1px solid #fecaca;border-radius:6px;
                     padding:10px;font-size:13px;margin:8px 0 0'>{error}</pre>
      </div>

      <div style='padding:12px 18px'>
        <h3 style='margin:0 0 8px'>Contexto adicional</h3>
        <table style='width:100%;border-collapse:collapse;font-size:13px'>
          {context_rows}
        </table>
      </div>

      <p style='margin:16px 18px;color:#6b7280;font-size:11px'>
        Generado automáticamente por scraper/alerts/alert.py — send_scraper_failure_alert
      </p>
    </body></html>
    """

    result = _send_email_via_resend(subject, html_body, EMAIL_TO)
    result["scraper_name"] = scraper_name
    result["timestamp_utc"] = now_utc.isoformat()

    if not result.get("sent"):
        logger.error("Fallo al enviar alerta de scraper (%s): %s", scraper_name, result.get("error"))
        _write_failed_alert_log(
            reason=result.get("error", "Motivo desconocido"),
            payload={
                "type": "scraper_failure_alert",
                "scraper_name": scraper_name,
                "error": error,
                "context": context,
                "to": EMAIL_TO,
                "subject": subject,
            },
        )
    else:
        logger.info("Alerta de fallo enviada para '%s' (email_id=%s)", scraper_name, result.get("email_id"))

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  Resumen de corrida exitosa (liviano — no bloqueante)
# ─────────────────────────────────────────────────────────────────────────────

def send_scraper_success_summary(scraper_name: str, stats: dict) -> dict:
    """
    Envía un resumen liviano por email cuando un scraper termina exitosamente,
    con estadísticas de la corrida (ej. cuántos productos se procesaron,
    cuánto tardó, etc.). Sirve para tener visibilidad aunque no haya fallado
    nada.

    A diferencia de `send_scraper_failure_alert`, esta función es NO
    bloqueante: si el envío falla, sólo se deja constancia en el log local
    (misma ruta `failed_alerts.log`) y se sigue de largo — no hace falta el
    mismo nivel de garantía que la alerta de fallo, porque no reportar un
    éxito no es tan grave como perder un aviso de fallo.

    Args:
        scraper_name: Nombre identificador del scraper.
        stats: Dict con estadísticas de la corrida (ej. {"productos": 342,
               "duracion_seg": 187, "pagina_final": 12}).

    Returns:
        Dict con el resultado del envío: {"sent": bool, "email_id"/"error": ...}.
        Nunca lanza excepciones.
    """
    try:
        now_utc = datetime.now(timezone.utc)
        subject = f"[Nike Dashboard] Corrida exitosa: {scraper_name}"

        stats_rows = "".join(
            f"<tr>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;color:#6b7280'>{k}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb'>{v}</td>"
            f"</tr>"
            for k, v in stats.items()
        ) or "<tr><td colspan='2' style='padding:6px 10px;color:#9ca3af'>(sin estadísticas)</td></tr>"

        html_body = f"""
        <html><body style='font-family:Arial,sans-serif;color:#111;max-width:700px;margin:0 auto'>
          <div style='background:#166534;color:#fff;padding:16px 20px;border-radius:10px 10px 0 0'>
            <h1 style='margin:0;font-size:20px'>Corrida exitosa — {scraper_name}</h1>
            <div style='opacity:0.8;font-size:13px;margin-top:4px'>
              Nike Dashboard · {now_utc.strftime('%d/%m/%Y %H:%M:%S UTC')}
            </div>
          </div>
          <div style='padding:12px 18px'>
            <table style='width:100%;border-collapse:collapse;font-size:13px'>
              {stats_rows}
            </table>
          </div>
          <p style='margin:16px 18px;color:#6b7280;font-size:11px'>
            Generado automáticamente por scraper/alerts/alert.py — send_scraper_success_summary
          </p>
        </body></html>
        """

        result = _send_email_via_resend(subject, html_body, EMAIL_TO)
        result["scraper_name"] = scraper_name
        result["timestamp_utc"] = now_utc.isoformat()

        if not result.get("sent"):
            logger.warning("No se pudo enviar el resumen de éxito de '%s': %s", scraper_name, result.get("error"))
            _write_failed_alert_log(
                reason=result.get("error", "Motivo desconocido"),
                payload={
                    "type": "scraper_success_summary",
                    "scraper_name": scraper_name,
                    "stats": stats,
                    "to": EMAIL_TO,
                    "subject": subject,
                },
            )
        else:
            logger.info("Resumen de éxito enviado para '%s' (email_id=%s)", scraper_name, result.get("email_id"))

        return result
    except Exception as e:
        # No-bloqueante: un resumen de éxito nunca debe tirar abajo el pipeline.
        logger.warning("send_scraper_success_summary falló de forma inesperada: %s", e)
        return {"sent": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  CLI — prueba manual
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Prueba manual del sistema de alertas de scrapers de Nike Dashboard"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Envía un email de prueba real (alerta de fallo + resumen de éxito) a EMAIL_TO",
    )
    args = parser.parse_args()

    if not args.test:
        parser.print_help()
        sys.exit(0)

    if not RESEND_API_KEY:
        print("RESEND_API_KEY no configurada, no se puede probar el envío real")
        sys.exit(1)

    print(f"RESEND_API_KEY configurada — probando envío real a {EMAIL_TO}...")

    print("\n--- send_scraper_failure_alert ---")
    failure_result = send_scraper_failure_alert(
        scraper_name="test-scraper",
        error="Timeout al esperar el selector '.product-card' (30s) — el sitio pudo haber cambiado su estructura HTML.",
        context={
            "productos_procesados": 87,
            "pagina": 4,
            "url": "https://www.nike.com.ar/ejemplo-de-prueba",
        },
    )
    print(json.dumps(failure_result, indent=2, ensure_ascii=False, default=str))

    print("\n--- send_scraper_success_summary ---")
    success_result = send_scraper_success_summary(
        scraper_name="test-scraper",
        stats={"productos": 342, "duracion_seg": 187, "pagina_final": 12},
    )
    print(json.dumps(success_result, indent=2, ensure_ascii=False, default=str))

    exit_code = 0 if failure_result.get("sent") and success_result.get("sent") else 1
    sys.exit(exit_code)
