# Imagen del motor de inteligencia (FastAPI + SQLite).
#
# El dashboard (web/) va a Vercel; esto NO puede ir a Vercel. No es preferencia:
# la base la construye `python -m app.pipeline` y vive en un archivo, y en un
# runtime serverless el filesystem es efímero y de sólo lectura. Necesita un
# contenedor de verdad — Render, Railway, Fly.
#
# La base NO viene en la imagen: `backend/data/processed/` está en .gitignore y
# los datos reales viven en Supabase. La construye `docker-entrypoint.sh` al
# arrancar, leyendo de $DATABASE_URL. Leé ese script antes de tocar el arranque:
# ahí está explicado por qué el pipeline se corre con `--keep`.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CI_DB_PATH=/data/intelligence.db

WORKDIR /app

# Las dependencias primero: cambian mucho menos que el código, así la capa se
# cachea entre deploys. `psycopg2-binary` viene en requirements.txt porque en el
# servidor la ingesta desde Supabase es EL camino, no un extra opcional.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x /app/docker-entrypoint.sh

# Directorio de la base. Con plan pago se le monta un disco encima y la base
# sobrevive a los reinicios; en el free tier queda en la capa efímera del
# contenedor y se reconstruye en cada arranque (~46 s, ver el entrypoint).
# Sin `VOLUME`: en Render el disco se declara en render.yaml, y declararlo acá
# haría que `docker run` cree volúmenes anónimos huérfanos en cada corrida.
RUN mkdir -p /data

EXPOSE 8000

# Healthcheck para quien corra la imagen fuera de Render (docker run, compose).
# Render usa el suyo, declarado en render.yaml como healthCheckPath.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.getenv('PORT','8000'), timeout=4)" || exit 1

CMD ["/app/docker-entrypoint.sh"]
