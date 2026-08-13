# Nike Analytics Dashboard

Dashboard de inteligencia competitiva para Nike Argentina — desarrollado por Southbay / Scout.

## Stack
- **Frontend**: Next.js 14 + TypeScript + Tailwind + Recharts
- **DB**: PostgreSQL (Supabase)
- **Scrapers**: Python 3.11 + requests/curl_cffi/Playwright
- **Deploy**: Vercel (frontend) + GitHub Actions (scrapers semanales)

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

## Deploy en Vercel

1. Importar repo en vercel.com
2. Root directory: `web`
3. Agregar env vars: `DATABASE_URL`, `NEXTAUTH_SECRET`, `NEXTAUTH_URL`
4. Deploy

## Scrapers (GitHub Actions)

Los scrapers corren automáticamente cada lunes a las 20:00 UTC.
También se pueden disparar manualmente desde la pestaña Actions.

Secrets requeridos en el repo:
- `DATABASE_URL`
- `OPENAI_API_KEY`
- `SMTP_USER` / `SMTP_PASS` (para alertas)
