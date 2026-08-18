# Nike Analytics Dashboard

Dashboard de inteligencia competitiva para Nike Argentina — desarrollado por Southbay / Scout.

## Stack
- **Frontend**: Next.js 14 + TypeScript + Tailwind + Recharts
- **DB**: PostgreSQL (Supabase)
- **Scrapers**: Python 3.11 + requests/curl_cffi/Playwright
- **Deploy**: Vercel (dashboard) + Render (motor de intelligence) + GitHub Actions (scrapers semanales)

## Dashboards
1. **Competencia** — Adidas & Puma: franchises, siluetas, BML, precios (D2C vs B2B)
2. **Share of Shelf** — Presencia por retailer (Nike vs Adidas vs Puma)
3. **Control Retailers** — PVP compliance, análisis markdown, BML vs nike.com.ar
4. **Assortment** — Product gap analysis, siluetas comparadas, omnicanalidad

## Login
- Email: `nike@southbay.com.ar`
- Password: `Nike2026!`

## Setup local

```bash
cd web
npm install
cp .env.local.example .env.local  # completar DATABASE_URL
npm run dev
```

Abrí http://localhost:3000

## Cargar datos a Supabase

```bash
cd db
pip install psycopg2-binary
DATABASE_URL=postgresql://... python load_csv.py ruta/al/pricing_combinado.csv
```

## Variables de entorno

```
DATABASE_URL=postgresql://postgres.xxx:password@aws-0-us-west-2.pooler.supabase.com:6543/postgres
NEXTAUTH_SECRET=nike-dashboard-secret-2026
NEXTAUTH_URL=https://tu-app.vercel.app
OPENAI_API_KEY=sk-...  (opcional, para clasificación IA de franchise/silueta)
SMTP_USER=...           (opcional, para alertas de scrapers)
SMTP_PASS=...
```

## Deploy

**Son dos piezas, y hay que desplegar las dos.** Vercel sirve el dashboard; el
motor de Competitive Intelligence es un servicio Python aparte que va a Render.
Con sólo Vercel, las 4 solapas de retail funcionan y las 6 de **Intelligence**
muestran un cartel diciendo que el motor no está configurado — es el síntoma de
"está todo roto" más reportado del proyecto, y la causa es simplemente esa.

📖 **Pasos exactos, con nombres de botón: [`docs/deploy.md`](docs/deploy.md)**

Resumen:

1. **Motor** → Render (free tier, sin tarjeta). *New +* → *Blueprint* → este
   repo → Root Directory `backend`. El repo ya trae `backend/render.yaml`.
   Variables a cargar: `DATABASE_URL` y `CI_API_KEY`.
   Verificar en `https://TU-MOTOR.onrender.com/api/health`.
2. **Dashboard** → Vercel. Importar repo, Root Directory `web`, env vars
   `DATABASE_URL`, `NEXTAUTH_SECRET`, `NEXTAUTH_URL`, más
   `INTELLIGENCE_API_URL` (la URL de Render) e `INTELLIGENCE_API_KEY` (el mismo
   valor que `CI_API_KEY`). **Redesplegar** después de agregarlas.

> Sin `CI_API_KEY` el motor queda **abierto en internet**: cualquiera con la URL
> lee todo el análisis competitivo.

## Scrapers (GitHub Actions)

Los scrapers corren automáticamente cada lunes a las 20:00 UTC.
También se pueden disparar manualmente desde la pestaña Actions.

Secrets requeridos en el repo:
- `DATABASE_URL`
- `OPENAI_API_KEY`
- `SMTP_USER` / `SMTP_PASS` (para alertas)
