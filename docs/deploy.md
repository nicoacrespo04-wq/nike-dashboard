# Deploy

Dos piezas que van a hosts distintos, y el motivo importa:

| Pieza | Dónde | Por qué |
|---|---|---|
| `web/` — el dashboard (10 solapas) | **Vercel** | Next.js, encaja natural |
| `backend/` — el motor de inteligencia | **Render / Railway / Fly** | Python + SQLite en disco |

**El motor no puede ir a Vercel.** No es una preferencia: la base la construye
`python -m app.pipeline` y vive en un archivo. En cualquier runtime serverless
el filesystem es efímero y de sólo lectura, así que el motor arrancaría vacío
después de cada invocación. Necesita un contenedor con disco persistente.

El browser **nunca** le pega al motor directamente: el dashboard lo consulta a
través de `/api/intelligence/[...path]`, un proxy que corre server-side en
Vercel. Eso deja un solo origen público, evita CORS, y —lo importante— permite
que la API key del motor viva sólo en el servidor y nunca llegue al navegador.

```
navegador ──► Vercel (web/)  ──► proxy /api/intelligence/*  ──► Render (backend/)
                    │                                              │
                    └──► Supabase (4 solapas de retail)            └──► disco /data
```

---

## 1. El motor, primero

Se despliega antes que el dashboard porque necesitás su URL para configurar Vercel.

Con **Render** (`backend/render.yaml` ya está listo):

1. Render → **New → Blueprint** → apuntá al repo.
2. **Root Directory: `backend`**.
3. Cargá las variables marcadas `sync: false`:
   - `CI_API_KEY` — generala con
     `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
     **Sin esta variable la API queda abierta** y cualquiera puede leer todo el
     análisis competitivo. En localhost da igual; en internet no.
   - `DATABASE_URL` — sólo si vas a ingerir los datos reales de Supabase.
     Sin esto el motor levanta con el dataset demo, que sirve para validar el
     deploy.
4. El plan **Starter** es el mínimo: el free tier no tiene disco persistente y
   perderías la base en cada reinicio.

Verificá que quedó bien:
```bash
curl https://TU-MOTOR.onrender.com/api/health
```
Tiene que responder `status: ok`, las 23 tablas con datos, y un bloque
`security` que confirme que la autenticación está activa.

Con **Railway** o **Fly** el `Dockerfile` es el mismo; lo único que hay que
replicar a mano es el volumen montado en `/data` y las variables de entorno.

## 2. El dashboard

1. Vercel → **Add New → Project** → importá el repo.
2. **Root Directory: `web`**.
3. Variables de entorno:

| Variable | Valor | Para qué |
|---|---|---|
| `DATABASE_URL` | connection string de Supabase | las 4 solapas de retail |
| `NEXTAUTH_SECRET` | string aleatorio largo | sesiones |
| `NEXTAUTH_URL` | `https://tu-app.vercel.app` | callbacks de login |
| `INTELLIGENCE_API_URL` | `https://TU-MOTOR.onrender.com` | las 6 solapas del motor |
| `INTELLIGENCE_API_KEY` | el mismo valor que `CI_API_KEY` | autenticación contra el motor |

Dos detalles que rompen deploys silenciosamente:

- **`INTELLIGENCE_API_KEY` va sin el prefijo `NEXT_PUBLIC_`.** Con ese prefijo,
  Next la mete en el bundle del cliente y la key queda expuesta en el navegador.
- **La contraseña del `DATABASE_URL` va URL-encodeada.** Los caracteres
  reservados (`@` → `%40`, `#` → `%23`, `:` → `%3A`) rompen el parseo de la
  connection string con un error que no dice nada útil.

## 3. Qué esperar si el motor no está

El diseño degrada a propósito: las 4 solapas de retail siguen funcionando
normal contra Supabase, y las 6 del motor muestran un estado con el motivo y
cómo levantarlo. Nunca una pantalla rota ni un loading infinito. Si desplegás
sólo Vercel, eso es exactamente lo que vas a ver.

## 4. Actualizar los datos

El pipeline es idempotente y el historial y el triaje **sobreviven al
recálculo** (`app/services/history.py` los rescata alrededor del reset), así
que se puede correr sin miedo a perder el estado que cargó el equipo:

```bash
python -m app.ingest --dsn $DATABASE_URL --incremental
python -m app.pipeline --keep
```

En `backend/render.yaml` hay un cron comentado que lo hace los lunes 21:00 UTC,
después de que corran los scrapers. Descomentalo cuando la ingesta esté andando.

Después de cada carga real conviene correr:
```bash
python -m app.calibration
```
Los 41 umbrales están calibrados contra el dataset demo; con miles de SKUs
reales algunos van a quedar inalcanzables o triviales, y el harness te dice
cuáles sin que tengas que mirarlos a ojo.
