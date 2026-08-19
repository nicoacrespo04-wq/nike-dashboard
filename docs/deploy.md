# Deploy

El dashboard son **dos piezas que van a dos hosts distintos**, y esa es la causa
del problema más reportado del proyecto: *"las solapas de Intelligence están
rotas"*. No están rotas — falta desplegar la segunda pieza.

| Pieza | Qué sirve | Dónde va |
|---|---|---|
| `web/` | Las 10 solapas del dashboard | **Vercel** |
| `backend/` | El motor que calcula 6 de esas 10 | **Render** |

Las 4 solapas de retail (Competencia, Share of Shelf, Control Retailers,
Assortment) leen Supabase directo y funcionan con sólo Vercel. Las 6 de
**Intelligence** (Overview, Products, Matches, Opportunities, Retail Media,
Brand) las calcula el motor, que es un servicio Python aparte. Si nadie lo
desplegó, esas 6 muestran un cartel explicando exactamente esto.

```
navegador ──► Vercel (web/) ──► proxy /api/intelligence/* ──► Render (backend/)
                   │                                              │
                   └──► Supabase (las 4 solapas de retail)         └──► Supabase (ingesta)
```

**El motor no puede ir a Vercel.** No es una preferencia: construye una base
SQLite en disco y la consulta entre requests. En un runtime serverless el
filesystem es efímero y de sólo lectura, así que no hay motor posible. Necesita
un contenedor.

El browser **nunca** le pega al motor directo: pasa por
`/api/intelligence/[...path]`, un proxy que corre server-side en Vercel. Eso deja
un solo origen público, evita CORS, y —lo importante— permite que la API key del
motor viva sólo en el servidor y nunca llegue al navegador.

---

## Antes de empezar

Tené a mano estas dos cosas, las vas a necesitar en el paso 1:

1. **La connection string de Supabase** (la misma `DATABASE_URL` que ya usa
   Vercel). Supabase → *Project Settings* → *Database* → *Connection string* →
   pestaña **URI**.
2. **Una clave inventada para el motor.** Sirve cualquier texto largo y random.
   Si tenés una terminal a mano:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Si no, sirve cualquier cosa larga que no sea adivinable. Vas a pegar **el
   mismo valor** en dos lugares (Render y Vercel) — copialo a un bloc de notas.

> **Por qué la clave no es opcional.** El motor queda con una URL pública en
> internet. Sin `CI_API_KEY` **no pide autenticación de ningún tipo**: cualquiera
> que dé con la URL se baja todo el análisis competitivo. Con la clave puesta,
> `backend/app/auth.py` exige el header `X-API-Key` en todo salvo `/api/health`.

---

## 1. Desplegar el motor en Render

Render es gratis para esto y no pide tarjeta. El repo ya trae todo configurado
(`render.yaml` en la raíz y `backend/Dockerfile`), así que no hay que escribir
nada ni elegir carpetas: el `rootDir: backend` va declarado adentro del propio
blueprint.

**Atajo — el link de un click:**

<https://render.com/deploy?repo=https://github.com/nicoacrespo04-wq/nike-dashboard>

Ese link abre Render directamente en la pantalla de crear el Blueprint con este
repo ya seleccionado. Te va a pedir iniciar sesión (lo más rápido es *Sign in
with GitHub*) y después saltá al punto 5 de acá abajo, que es lo único que hay
que completar a mano.

Si preferís hacerlo a mano, o el link no te lleva a ningún lado:

1. Entrá a [render.com](https://render.com) y creá una cuenta (**Get Started**;
   lo más rápido es *Sign in with GitHub*).
2. En el panel, botón **New +** (arriba a la derecha) → **Blueprint**.
3. Render te muestra tus repos de GitHub. Buscá este repo y tocá **Connect**.
   - Si no aparece: **Configure account** → dale permiso a Render sobre el repo.
4. En la pantalla que aparece:
   - **Blueprint Name**: poné lo que quieras (ej. `nike-intelligence`).
   - **Branch**: `main`.
   - No busques un campo **Root Directory**: los Blueprints no lo tienen. Render
     lee `render.yaml` de la raíz del repo y de ahí saca `rootDir: backend`.
5. Más abajo, Render pide las variables marcadas como secretas. Son **dos**:

   | Campo | Qué pegar |
   |---|---|
   | `DATABASE_URL` | La connection string de Supabase del paso *Antes de empezar* |
   | `CI_API_KEY` | La clave larga que inventaste |

6. Botón **Apply** (o **Create New Resources**).

Render construye la imagen y levanta el servicio. **La primera vez tarda entre 5
y 10 minutos** (tiene que instalar pandas, numpy y scikit-learn). Vas viendo los
logs en vivo; cuando arriba diga **Live** en verde, terminó.

Anotá la URL que te asigna, arriba de todo en la página del servicio. Tiene esta
forma:

```
https://nike-intelligence-api.onrender.com
```

### Verificá que el motor quedó bien

Abrí en el navegador la URL del servicio **con `/api/health` al final**:

```
https://nike-intelligence-api.onrender.com/api/health
```

Vas a ver un JSON. Mirá estos tres campos:

```json
{
  "status": "ok",
  "data": {
    "status": "ok",
    "products": 995,
    "expected_source": "supabase"
  },
  "security": { "auth_required": true }
}
```

| Qué ves | Qué significa | Qué hacer |
|---|---|---|
| `"status": "ok"` con `products` > 0 | Todo bien | Seguí al paso 2 |
| `"status": "building"` | Está cargando los datos (tarda ~1 min) | Esperá y recargá |
| `"status": "empty"` | No cargó nada | Revisá que `DATABASE_URL` esté bien copiada (ver *Si algo falla*) |
| `"expected_source": "demo"` | Le falta `DATABASE_URL` | Cargala en **Environment** y **Manual Deploy → Deploy latest commit** |
| `"auth_required": false` | **El motor está abierto al público** | Cargá `CI_API_KEY` en **Environment** |

Este endpoint es público a propósito (no pide clave): es el que usa el dashboard
para saber si el motor está vivo. No expone datos, sólo contadores.

---

## 2. Conectar el dashboard al motor

Ahora que tenés la URL, hay que decírsela a Vercel.

1. Entrá a [vercel.com](https://vercel.com) → abrí el proyecto del dashboard.
2. **Settings** (arriba) → **Environment Variables** (menú de la izquierda).
3. Agregá estas dos, una por vez, con **Add Another** entre medio:

   | Key | Value |
   |---|---|
   | `INTELLIGENCE_API_URL` | La URL de Render, **sin barra al final**: `https://nike-intelligence-api.onrender.com` |
   | `INTELLIGENCE_API_KEY` | **El mismo valor** que pusiste en `CI_API_KEY` en Render |

   Dejá los tres entornos tildados (*Production*, *Preview*, *Development*) y
   tocá **Save**.

4. **Las variables nuevas no se aplican solas: hay que redesplegar.**
   Andá a **Deployments**, en el deploy más reciente tocá el menú **⋯** →
   **Redeploy** → confirmá con **Redeploy**.

Dos detalles que rompen esto en silencio:

- **`INTELLIGENCE_API_KEY` va SIN el prefijo `NEXT_PUBLIC_`.** Con ese prefijo
  Next la mete en el bundle del navegador y la clave queda a la vista de
  cualquiera con las devtools abiertas.
- **La URL va sin `/` al final y sin `/api`.** El proxy le agrega `/api` solo.
  `https://...onrender.com/api` termina pegándole a `/api/api/health`.

---

## 3. Verificar que anduvo

1. Abrí el dashboard y entrá a **Intelligence → Overview**.
2. Arriba de la pantalla hay una cinta de estado. Tiene que decir
   *"Pipeline: N/17 tablas con datos"*. Si dice **"El motor está cargando sus
   datos"**, esperá un minuto y recargá — se está despertando.
3. Los KPIs de arriba tienen que mostrar números reales (cientos de productos,
   miles de matches), no ceros y no 45 productos.

Si ves **45 productos**, el motor está corriendo con el dataset de demostración:
le falta `DATABASE_URL`. Volvé al final del paso 1.

---

## Lo que hay que saber del plan free

Son dos cosas, y conviene saberlas antes de que sorprendan:

1. **El servicio se duerme tras 15 minutos sin uso.** La primera visita después
   de la siesta tarda alrededor de un minuto: Render levanta el contenedor y el
   motor reconstruye su base. Durante ese rato el dashboard muestra *"El motor
   está cargando sus datos"* en vez de romperse. Las visitas siguientes son
   instantáneas.
2. **Los datos se refrescan solos.** Como el plan free no tiene disco, cada vez
   que el motor despierta vuelve a leer Supabase. Siempre ves lo último que
   cargaron los scrapers, sin cron ni mantenimiento.

Si ese minuto de espera molesta, la salida es el plan **Starter** (pago) con
disco persistente: el arranque pasa a ser instantáneo. Está todo explicado y
listo para descomentar en `render.yaml` — son cuatro líneas, más el cron
semanal que ahí pasa a ser necesario (con disco, la base ya no se refresca sola).

---

## Si algo falla

| Síntoma | Causa casi siempre | Solución |
|---|---|---|
| El dashboard dice *"falta la variable INTELLIGENCE_API_URL"* | Se cargó la variable pero no se redesplegó | Vercel → **Deployments** → **⋯** → **Redeploy** |
| El dashboard dice *"está configurado pero no respondió"* | El servicio está dormido | Recargá; si sigue, abrí `/api/health` a ver qué contesta |
| `/api/health` da `"status": "empty"` | `DATABASE_URL` mal copiada | Ver abajo, la contraseña |
| El build de Render falla | Casi siempre el Root Directory | Tiene que ser `backend`, no la raíz |
| Todo anda pero hay 45 productos | Falta `DATABASE_URL` en Render | Cargala y redesplegá |

**La contraseña del `DATABASE_URL` va URL-encodeada.** Es el error más común y no
avisa con un mensaje útil: si tu contraseña de Supabase tiene `@`, `#` o `:`,
hay que escribirlos como `%40`, `%23` y `%3A`. Ejemplo: la contraseña `p@ss#1`
va en la URL como `p%40ss%231`.

**Los logs del motor** están en Render → tu servicio → **Logs**. Buscá las líneas
que empiezan con `[bootstrap]`: dicen si la ingesta corrió, cuántas filas leyó y
si falló.

---

## Actualizar los datos a mano

En el plan free no hace falta (cada despertar reingesta). Con disco persistente,
o si querés forzar una recarga:

```bash
python -m app.ingest --dsn "$DATABASE_URL" --incremental
python -m app.pipeline --keep --source ingest
```

> ⚠️ **`--keep` no es opcional.** Sin él, el pipeline corre su etapa `seed`, que
> borra la base entera y siembra el dataset demo de 45 productos **encima de los
> datos reales, en silencio y sin un solo error en los logs**. Fue un incidente
> real; está documentado en `backend/app/pipeline.py` y cubierto en
> `backend/tests/test_pipeline.py`.

El pipeline es idempotente, y el historial y el triaje sobreviven al recálculo
(`app/services/history.py` los rescata alrededor del reset), así que se puede
correr sin miedo a perder el estado que cargó el equipo.

Después de una carga real conviene correr:

```bash
python -m app.calibration
```

Los 41 umbrales están calibrados contra el dataset demo; con miles de SKUs reales
algunos quedan inalcanzables o triviales, y el harness te dice cuáles.

---

## Correr el motor en tu máquina

Para desarrollo no hace falta nada de lo anterior:

```bash
cd backend
pip install -r requirements.txt
python -m app.pipeline                      # dataset demo, 45 productos
uvicorn app.main:app --port 8000
```

Con datos reales, apuntando a tu Postgres:

```bash
python -m app.ingest --dsn "$DATABASE_URL"
python -m app.pipeline --keep --source ingest
uvicorn app.main:app --port 8000
```

`web/` toma `http://localhost:8000` por defecto, así que `npm run dev` lo
encuentra sin configurar nada.

Con Docker, el mismo arranque que corre en Render:

```bash
cd backend
docker build -t nike-intelligence .
docker run -p 8000:8000 -e DATABASE_URL="postgresql://..." nike-intelligence
```
