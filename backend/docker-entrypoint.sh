#!/bin/sh
# Arranque del motor de inteligencia dentro del contenedor.
#
# El motor sirve una base SQLite que NO está en el repo (`backend/data/processed/`
# está en .gitignore) y que en el servidor no existe: hay que construirla. Este
# script decide cuándo hacerlo y con qué datos, y después levanta la API.
#
# ── La secuencia de construcción, y por qué es exactamente esta ──────────────
#
#     python -m app.ingest  --dsn "$DATABASE_URL"      # Supabase -> SQLite
#     python -m app.pipeline --keep --source ingest    # scoring sobre lo ingerido
#
# `--keep` NO ES OPCIONAL. Sin él, el pipeline corre su etapa `seed`, que hace
# `init_db(drop=True)` y BORRA EL ARCHIVO ENTERO para sembrar el dataset demo de
# 45 productos. O sea: la ingesta de 70.000 filas reales se pierde en silencio y
# todo el análisis pasa a salir de datos de mentira, sin un solo error en los
# logs. Fue un incidente real; está documentado en `app/pipeline.py` (la guarda
# que omite `seed` cuando no hay `reset`) y cubierto en `tests/test_pipeline.py`.
#
# ── Por qué se construye en cada arranque ────────────────────────────────────
#
# En el free tier de Render el disco es EFÍMERO: no hay volumen que montar, así
# que todo lo que se escriba muere con el contenedor. El servicio además se
# duerme tras 15 minutos sin tráfico, con lo cual "arranque en frío" no es un
# evento raro sino la rutina diaria de un dashboard que se mira dos veces al día.
#
# Medido en esta máquina contra un Postgres con 70.000 filas en `pricing_data`:
#
#     ingest    20 s   -> 995 productos, 27.593 precios, 28.583 stock
#     pipeline  26 s   -> 4.000 matches, 1.447 oportunidades, 3.053 de retail media
#     ---------------
#     total     46 s
#
# 46 s entra cómodo en un arranque, PERO no se puede bloquear el puerto ese
# tiempo: Render espera que el proceso ligue $PORT para dar el deploy por bueno,
# y cualquier request que entre mientras tanto moriría por connection refused.
# Por eso la construcción va EN SEGUNDO PLANO y uvicorn liga el puerto de una:
# durante esos 46 s el motor responde, `/api/health` dice `status: building`, y
# el dashboard muestra "el motor está cargando" en vez de "el motor está caído".
#
# Con disco persistente (plan pago, ver render.yaml) `CI_BOOTSTRAP=auto` detecta
# que la base ya está construida y no reconstruye nada: el arranque es instantáneo
# y los datos se refrescan con el cron.
#
# ── Variables ────────────────────────────────────────────────────────────────
#
#   DATABASE_URL   Postgres/Supabase de origen. SIN esta variable el motor
#                  levanta con el dataset DEMO de 45 productos — sirve para
#                  validar que el deploy anduvo, no para tomar decisiones.
#   CI_DB_PATH     Ruta del SQLite (default /data/intelligence.db).
#   CI_BOOTSTRAP   auto (default) | always | never
#                    auto   -> construye sólo si la base no existe o está vacía
#                    always -> reconstruye en cada arranque
#                    never  -> no construye nunca (la base la carga un job aparte)
#   CI_INGEST_ARGS Argumentos extra para la ingesta (ej: "--country AR --limit 5000").
#   PORT           Lo inyecta Render. Default 8000.

set -e

DB="${CI_DB_PATH:-/data/intelligence.db}"
BOOTSTRAP="${CI_BOOTSTRAP:-auto}"
PORT="${PORT:-8000}"

# Se re-exporta aunque ya venga del entorno, para el caso en que NO venga: el
# default de este script (/data/intelligence.db) y el de `app.config.DB_PATH`
# (data/processed/intelligence.db) son distintos. Sin esto, con CI_DB_PATH sin
# definir, el pipeline escribiría en un archivo y `build_state` y uvicorn
# leerían otro: el motor serviría una base vacía para siempre mientras los logs
# muestran una ingesta perfecta.
export CI_DB_PATH="$DB"

mkdir -p "$(dirname "$DB")"

# ¿Hay que construir? Con `auto` se le pregunta al propio motor si la base tiene
# productos, en vez de mirar si el archivo existe: un archivo de 0 bytes de un
# arranque anterior que se cortó a la mitad existe igual, y saltearlo dejaría el
# servicio sirviendo una base vacía para siempre.
needs_build() {
    case "$BOOTSTRAP" in
        never)  return 1 ;;
        always) return 0 ;;
    esac
    [ -f "$DB" ] || return 0
    python - "$DB" <<'PY' || return 0
import sqlite3, sys
try:
    con = sqlite3.connect(sys.argv[1])
    n = con.execute("select count(*) from products").fetchone()[0]
except Exception:
    sys.exit(1)          # base ilegible o sin esquema -> hay que construirla
sys.exit(0 if n > 0 else 1)
PY
    return 1
}

build() {
    python -c "from app import build_state; build_state.write('building', detail='arranque del contenedor')"

    if [ -n "$DATABASE_URL" ]; then
        echo "[bootstrap] ingesta desde Postgres -> $DB"
        # shellcheck disable=SC2086
        if ! python -m app.ingest --dsn "$DATABASE_URL" --db "$DB" $CI_INGEST_ARGS; then
            echo "[bootstrap] ERROR: la ingesta falló. La base queda como estaba." >&2
            python -c "from app import build_state; build_state.write('failed', detail='la ingesta desde DATABASE_URL falló')"
            return 1
        fi

        echo "[bootstrap] pipeline sobre los datos ingeridos (--keep)"
        # --keep y --source ingest: ver el comentario largo de arriba. Cambiar
        # esta línea por `python -m app.pipeline` a secas borra los datos reales.
        if ! python -m app.pipeline --keep --source ingest --db "$DB"; then
            echo "[bootstrap] ERROR: el pipeline falló." >&2
            python -c "from app import build_state; build_state.write('failed', detail='el pipeline falló después de la ingesta')"
            return 1
        fi
    else
        echo "[bootstrap] DATABASE_URL no está definida: se levanta el dataset DEMO (45 productos)." >&2
        # Sin --keep a propósito: acá SÍ queremos que `seed` siembre el demo.
        if ! python -m app.pipeline --db "$DB"; then
            python -c "from app import build_state; build_state.write('failed', detail='el pipeline demo falló')"
            return 1
        fi
        python -c "from app import build_state; build_state.write('ready', detail='dataset demo — falta DATABASE_URL para los datos reales')"
        return 0
    fi

    python -c "from app import build_state; build_state.write('ready', detail='ingesta desde DATABASE_URL')"
    echo "[bootstrap] listo."
}

if needs_build; then
    # En segundo plano para no demorar el bind del puerto (ver arriba). El fallo
    # no tumba el contenedor: queda registrado en el estado y `/api/health` lo
    # reporta — un motor que responde "no pude cargar los datos" es infinitamente
    # más diagnosticable que un contenedor en crash-loop.
    ( build || true ) &
elif [ "$BOOTSTRAP" = "never" ]; then
    # Mensaje aparte del de abajo a propósito: con `never` la base puede estar
    # perfectamente vacía y el motor va a servir 0 productos. Decir "ya tiene
    # datos" acá sería mentir en el único log que alguien va a leer cuando el
    # dashboard aparezca vacío.
    echo "[bootstrap] CI_BOOTSTRAP=never: no se construye nada. La base la tiene que cargar un job aparte."
else
    echo "[bootstrap] la base ya tiene datos: no se reconstruye (CI_BOOTSTRAP=$BOOTSTRAP)."
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
