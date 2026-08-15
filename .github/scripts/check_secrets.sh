#!/usr/bin/env bash
#
# check_secrets.sh — Preflight del workflow de scrapers.
#
# Falla RÁPIDO y CLARO si falta un secret obligatorio, en vez de romperse a
# mitad de camino (checkout que no autoriza, o 40 minutos de scraping para
# después no poder cargar nada a la base).
#
# Lee sólo variables de entorno (nunca imprime el valor de un secret):
#   SCRAPERS_REPO_TOKEN  — obligatorio: PAT de lectura del repo de scrapers
#   DATABASE_URL         — obligatorio: Postgres/Supabase destino
#   SMTP_USER / SMTP_PASS— opcionales: sin ellos no salen los emails
#   OPENAI_API_KEY       — opcional: sin él no hay clasificación IA
#   SCRAPERS_REPO        — informativo
#
# Uso local:
#   SCRAPERS_REPO_TOKEN=x DATABASE_URL=y bash .github/scripts/check_secrets.sh
set -euo pipefail

missing=()
warnings=()

req() { # req NOMBRE VALOR "cómo obtenerlo"
  if [ -z "${2:-}" ]; then
    missing+=("$1|$3")
  else
    echo "  [OK]    $1 definido"
  fi
}

opt() { # opt NOMBRE VALOR "qué se pierde si falta"
  if [ -z "${2:-}" ]; then
    warnings+=("$1|$3")
    echo "  [WARN]  $1 no definido"
  else
    echo "  [OK]    $1 definido"
  fi
}

echo "── Preflight de secrets ──────────────────────────────────"
echo "  Repo de scrapers: ${SCRAPERS_REPO:-<sin definir>}"

req SCRAPERS_REPO_TOKEN "${SCRAPERS_REPO_TOKEN:-}" \
  "PAT con acceso de lectura a ${SCRAPERS_REPO:-el repo de scrapers}. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Repository access: sólo ese repo → Permissions → Contents: Read-only. Guardalo en este repo como secret SCRAPERS_REPO_TOKEN."
req DATABASE_URL "${DATABASE_URL:-}" \
  "Connection string de Postgres/Supabase (Supabase → Project Settings → Database → Connection string → URI). Guardalo como secret DATABASE_URL."

opt SMTP_USER "${SMTP_USER:-}" "no se envía el email de resumen ni las alertas"
opt SMTP_PASS "${SMTP_PASS:-}" "no se envía el email de resumen ni las alertas"
opt OPENAI_API_KEY "${OPENAI_API_KEY:-}" "los adapters no clasifican franchise con IA (se cae a heurísticas)"

summary_file="${GITHUB_STEP_SUMMARY:-/dev/null}"
{
  echo "### Preflight de secrets"
  echo ""
} >> "$summary_file"

if [ ${#warnings[@]} -gt 0 ]; then
  for w in "${warnings[@]}"; do
    name="${w%%|*}"; hint="${w#*|}"
    echo "::warning::Secret opcional ausente: ${name} — ${hint}"
    echo "- :warning: \`${name}\` ausente — ${hint}" >> "$summary_file"
  done
fi

if [ ${#missing[@]} -gt 0 ]; then
  echo ""
  echo "FALTAN SECRETS OBLIGATORIOS. La corrida se aborta acá a propósito."
  for m in "${missing[@]}"; do
    name="${m%%|*}"; hint="${m#*|}"
    echo "::error::Falta el secret obligatorio ${name}. ${hint}"
    echo "- :x: **${name}** — ${hint}" >> "$summary_file"
  done
  echo "" >> "$summary_file"
  echo "Cargalos en: Settings → Secrets and variables → Actions → New repository secret." >> "$summary_file"
  exit 1
fi

echo ""
echo "Todos los secrets obligatorios están presentes."
echo "- :white_check_mark: Secrets obligatorios presentes" >> "$summary_file"
