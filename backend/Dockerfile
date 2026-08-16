# Imagen del motor de inteligencia (FastAPI + SQLite).
#
# El dashboard (web/) va a Vercel; esto va a un host con disco persistente
# —Render, Railway, Fly— porque la base se construye con el pipeline y tiene
# que sobrevivir a los reinicios. En un filesystem efímero el motor arranca
# vacío después de cada deploy.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CI_DB_PATH=/data/intelligence.db

WORKDIR /app

# Las dependencias primero: cambian mucho menos que el código, así la capa
# se cachea entre deploys.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Disco persistente. El host tiene que montar un volumen acá; si no, la base
# se pierde en cada reinicio y el dashboard muestra las solapas vacías.
VOLUME ["/data"]

EXPOSE 8000

# Si la base no existe todavía, se construye antes de levantar la API. Con
# volumen montado esto corre una sola vez; en los reinicios siguientes
# arranca directo.
CMD ["sh", "-c", "[ -f \"$CI_DB_PATH\" ] || python -m app.pipeline; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
