# Scrapers — cómo funciona el flujo semanal

Todo lo que corre los lunes 20:00 UTC (17:00 AR) y termina en `pricing_data`
de Supabase, en un solo lugar.

---

## 1. El mapa: dos repos, un workspace

Los scrapers **no viven en este repo**. Viven en un repo privado aparte:

| Repo | Qué tiene |
|---|---|
| `nicoacrespo04-wq/nike-dashboard` (**este**) | `db/load_csv.py` (cargador a Supabase), `scraper/` (adapters + alertas), `web/` (dashboard), `.github/` (workflow) |
| `nicoacrespo04-wq/Nike_Scrapper_Final` (**privado**) | `codigo_nike_ar_general.py`, `codigo_adidas_7.py`, `codigo_puma.py`, los 8 `codigo_<retailer>.py` y `combine_outputs.py` |

El workflow `.github/workflows/scraper.yml` clona **los dos** en el mismo
workspace del runner:

```
$GITHUB_WORKSPACE/
├── .github/            ← este repo
│   ├── actions/setup-scrapers/   (checkout + python + deps + playwright)
│   ├── scripts/check_secrets.sh
│   ├── scripts/load_and_report.py
│   ├── scripts/send_run_summary.py
│   └── workflows/scraper.yml
├── db/load_csv.py      ← este repo (cargador + saneamiento de precios)
├── scraper/            ← este repo (adapters, alertas)
└── Nike_Scrapper_Final/  ← repo privado, clonado con un PAT
    ├── codigo_*.py
    └── combine_outputs.py
```

> **Por qué así.** Los scrapers ya andan y están en el otro repo; el cargador,
> el schema y el dashboard están acá. Traer el repo de scrapers al runner con
> un segundo `actions/checkout` es la opción que menos mueve y la única que
> deja todo funcionando hoy. Las alternativas evaluadas fueron: migrar los
> scrapers a `scraper/adapters/` (imposible ahora — los originales no están
> en este repo; queda como camino largo, ver §7) y mover el workflow al repo
> de scrapers (entonces el problema se invierte: habría que clonar *este*
> repo para poder cargar a la base, y los secrets quedarían repartidos).

### Flujo de un job

```
preflight (secrets) → checkout x2 → pip install → scraper → verificar CSV → load_csv.py → artifacts
                                                                     ↓
                                                        run-metrics/<job>.json
                                                                     ↓
                                                    notify → Job Summary + email
```

---

## 2. Jobs del workflow

| Job | Qué corre | Notas |
|---|---|---|
| `preflight` | `.github/scripts/check_secrets.sh` | Falla en 30 segundos con un mensaje claro si falta un secret, en vez de reventar a mitad de corrida. Todos los demás jobs dependen de él. |
| `nike-ar` | `codigo_nike_ar_general.py` | Precios de referencia de nike.com.ar. |
| `competencia` | `codigo_adidas_7.py`, `codigo_puma.py`, `combine_outputs.py` | Adidas y Puma corren igual aunque el otro falle; el job termina en rojo si alguno falló. Se carga `pricing_combinado_*.csv`. |
| `retailers-ar` | matriz de 8 retailers | `fail-fast: false`: un retailer caído no arrastra a los otros. |
| `notify` | `.github/scripts/send_run_summary.py` | Junta las métricas de todos los jobs, escribe el Job Summary y manda el email. |

Cada job de scraping, después de correr el scraper, ejecuta
`.github/scripts/load_and_report.py`, que:

1. resuelve el glob del CSV **en Python** y **case-insensitive** (los nombres
   de archivo de los retailers no son uniformes);
2. descarta archivos que no se hayan generado en esta corrida (así no se
   recarga un CSV viejo que esté commiteado en el repo de scrapers);
3. **verifica que el CSV exista y tenga al menos una fila de datos** — si no,
   falla con un error explícito y lista qué CSVs sí hay en la carpeta;
4. corre `db/load_csv.py` y **parsea su resumen** (filas insertadas, filas
   rechazadas, precios descartados/corregidos);
5. escribe `run-metrics/<job>.json`, que se sube como artifact y alimenta el
   email final.

Nada de `continue-on-error` global: los pasos de scraping sí lo tienen (para
poder cargar lo que haya y para que un retailer no tape al otro), pero cada
job termina en **rojo** si su scraper falló, si no hubo CSV, si el CSV estaba
vacío o si el loader no insertó nada.

---

## 3. Secrets

En **este** repo: *Settings → Secrets and variables → Actions → New repository secret*.

| Secret | Obligatorio | Cómo obtenerlo |
|---|---|---|
| `SCRAPERS_REPO_TOKEN` | **Sí** | GitHub → *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token*. **Repository access**: sólo `nicoacrespo04-wq/Nike_Scrapper_Final`. **Permissions**: `Contents: Read-only`. Copiá el token (se muestra una sola vez). Alternativa: token clásico con scope `repo`, pero da mucho más permiso del necesario. Los fine-grained vencen: anotá la fecha, cuando expire el checkout empieza a fallar con 404. |
| `DATABASE_URL` | **Sí** | Supabase → *Project Settings → Database → Connection string → URI*. Reemplazá `[YOUR-PASSWORD]` por la password real. Usá el pooler (`...pooler.supabase.com:6543`) para cargas por lote. |
| `SMTP_USER` | No (sin él no hay email) | Cuenta Gmail que envía las alertas (ej. `alertas.nike@gmail.com`). |
| `SMTP_PASS` | No | **App password** de esa cuenta de Google (requiere 2FA): *Cuenta de Google → Seguridad → Verificación en 2 pasos → Contraseñas de aplicaciones*. La password normal de Gmail **no** funciona. |
| `OPENAI_API_KEY` | No | Sólo para clasificación de franchise con IA en los adapters. Sin él se usan heurísticas. |

Sin `SMTP_USER`/`SMTP_PASS` el workflow **no falla**: avisa con un warning y el
resumen queda igual en el Job Summary de la corrida.

Cosas que **no** son secrets y están fijas en el `env` del workflow:
`SCRAPERS_REPO`, `SCRAPERS_DIR`, `ALERT_EMAIL`, `SMTP_HOST/PORT`, `TRUNCATE` y
las `PRICE_*`.

---

## 4. Saneamiento de precios (ahí se limpian los datos)

`db/load_csv.py` corrige la basura conocida de los CSV antes de insertar:

* **precios multiplicados por las cuotas** (ej. `2.639.992 = 329.999 × 8`): si
  el precio cae fuera del rango plausible y la fila declara N cuotas, se
  divide por N;
* **precios en `0` o negativos**: no son "gratis", son dato ausente → `NULL`;
* **fuera de rango sin explicación** → `NULL` (mejor N/D que un número que el
  negocio no puede usar);
* si un precio de competidor se tocó, se **anulan sus derivados** (gaps, BML,
  price index, USD) para que la UI muestre N/D en vez de un número calculado
  sobre basura.

Está expuesto en el `env` del workflow con los valores por defecto explícitos:

| Variable | Default | Qué hace |
|---|---|---|
| `PRICE_SANITIZE` | `true` | `false` desactiva todo el saneamiento. |
| `PRICE_MIN_ARS` | `1000` | Piso plausible de un precio en ARS. |
| `PRICE_MAX_ARS` | `2000000` | Techo plausible de un precio en ARS. |
| `PRICE_MAX_CUOTAS` | `24` | Máximo de cuotas que se acepta parsear. |
| `PRICE_INVALIDATE_DERIVED` | `true` | Anula gaps/BML/USD de las filas con precio corregido. |

Cuántos precios se descartaron y cuántos se recuperaron sale en el log del
job, en el Job Summary y en el email de resumen. El mismo criterio está
replicado en la capa de lectura (`web/src/lib/price.ts`): si cambia uno, hay
que cambiar el otro.

---

## 5. Correr un scraper a mano

### a) Desde GitHub (lo normal)

*Actions → Nike Dashboard — Weekly Scrapers → Run workflow*. Dos inputs:

* **`allow_stale_csv`** — por defecto sólo se cargan CSVs generados en esa
  corrida. Marcalo si querés recargar CSVs que ya existían en el repo de
  scrapers (por ejemplo, para reprocesar una corrida vieja con el saneamiento
  de precios activo).
* **`scrapers_ref`** — branch/tag/SHA del repo de scrapers, para probar un fix
  de un scraper sin mergearlo a su `main`.

### b) En tu máquina

```bash
# 1. Los dos repos, hermanos
git clone git@github.com:nicoacrespo04-wq/nike-dashboard.git
git clone git@github.com:nicoacrespo04-wq/Nike_Scrapper_Final.git

cd nike-dashboard
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium               # sólo si el scraper lo usa

# 2. Correr el scraper (deja el CSV en su propia carpeta)
cd ../Nike_Scrapper_Final
python codigo_dexter.py

# 3. Cargarlo, con la misma verificación que hace CI
cd ../nike-dashboard
export DATABASE_URL='postgresql://...'
python .github/scripts/load_and_report.py \
    --name dexter --label Dexter --loader db/load_csv.py \
    --metrics-dir /tmp/metrics --min-mtime 0 \
    '../Nike_Scrapper_Final/*dexter*.csv'
```

O directo, sin verificación:
`DATABASE_URL=... python db/load_csv.py ../Nike_Scrapper_Final/dexter_....csv`

Para probar el saneamiento sin escribir nada raro, jugá con las `PRICE_*`:
`PRICE_SANITIZE=false DATABASE_URL=... python db/load_csv.py archivo.csv`.

### c) Probar el email de resumen

```bash
SMTP_USER=... SMTP_PASS=... ALERT_EMAIL=vos@dominio.com \
python .github/scripts/send_run_summary.py \
    --metrics-dir /tmp/metrics --job "nike-ar=success"
```

Agregá `--no-email` para ver sólo el resumen por consola.

---

## 6. Agregar un retailer nuevo (flujo actual, con scripts legacy)

1. Escribí el `codigo_<retailer>.py` en el repo `Nike_Scrapper_Final`. Que
   deje un CSV con el nombre del retailer en el nombre del archivo y con las
   columnas del `COL_MAP` de `db/load_csv.py` (podés escribir un subconjunto:
   las que falten quedan `NULL`).
2. En `.github/workflows/scraper.yml`, agregá una línea a la matriz:
   ```yaml
   - { name: nuevoretailer, script: codigo_nuevoretailer.py }
   ```
   No hace falta tocar nada más: el paso de carga arma los patrones
   `*<name>*_vs_nike_*.csv` y `*<name>*.csv` a partir del nombre.
3. Registralo en `RETAILERS` (`scraper/adapters/__init__.py`) como pendiente
   de migración, con su script legacy y su canal.
4. Corré el workflow a mano y mirá el Job Summary: si el patrón no matcheó, el
   log lista todos los CSVs que hay en la carpeta para que ajustes el nombre.

**Precios**: cargá el precio unitario, nunca el total en cuotas, y usá vacío
(no `0`) cuando no hay dato. Si el sitio muestra "N cuotas sin interés", poné
N en `Cuotas_Competitor` — el loader lo usa para recuperar precios inflados.

---

## 7. Migrar un retailer a `scraper/adapters/` (camino largo)

`scraper/adapters/` tiene la infraestructura común: cascada de fetch
(`requests → curl_cffi → Playwright`), retries con backoff, rotación de User
Agent, clasificación de silueta/franchise, alertas por email y escritura
directa a `pricing_data`.

Estado hoy:

| Retailer | Adapter | Script legacy |
|---|---|---|
| `nike_ar` | ✅ `NikeARAdapter` | `codigo_nike_ar_general.py` |
| `adidas` | ✅ `AdidasARAdapter` | `codigo_adidas_7.py` |
| `puma` | ⬜ pendiente | `codigo_puma.py` |
| `dexter` | ⬜ pendiente | `codigo_dexter.py` |
| `moov` | ⬜ pendiente | `codigo_moov.py` |
| `sporting` | ⬜ pendiente | `codigo_sporting3.py` |
| `soloDeportes` | ⬜ pendiente | `codigo_soloDeportes.py` |
| `grid` | ⬜ pendiente | `codigo_grid.py` |
| `dash` | ⬜ pendiente | `codigo_dash.py` |
| `opensports` | ⬜ pendiente | `codigo_opensports.py` |
| `stockcenter` | ⬜ pendiente | `codigo_stockcenter_v6.py` |

`python scraper/run_adapter.py --list` imprime esta misma tabla desde el
código (`RETAILERS` en `scraper/adapters/__init__.py`).

Para migrar uno, copiá `scraper/adapters/template_adapter.py` y seguí el
checklist de su docstring. Resumen de lo que hay que implementar por adapter:

* `SCRAPER_NAME` **igual al del script legacy** (es el valor de la columna
  `Scraper` en `pricing_data`; si cambia, se corta el histórico);
* `CANAL`, `MARCA`, `BASE_URL`;
* `scrape()` → `List[ScrapedProduct]`, paginando la API del sitio (la mayoría
  de los retailers argentinos son VTEX: `/api/catalog_system/pub/products/search`)
  y usando `self.get()` para tener los fallbacks gratis;
* precios: unitario, `None` cuando falta, y `cuotas_competitor` cuando el
  sitio publica cuotas;
* talles: cantidad con stock + texto;
* `silueta`/`division` vía `self.classify_silueta()`;
* comparación contra Nike (`nike_final_price`, `calculate_gap_pct()`,
  `calculate_bml()`) si el scraper matchea catálogos;
* registrarlo en `RETAILERS` con `adapter=<TuClase>`.

Probalo con:

```bash
python scraper/run_adapter.py puma --limit 20 --csv /tmp/puma.csv
```

y compará ese CSV contra el que produce el script legacy **antes** de cambiar
el workflow. Recién ahí se reemplaza el paso `python codigo_puma.py` por
`python scraper/run_adapter.py puma --csv ...`.

---

## 8. Troubleshooting

| Síntoma en el log | Qué pasó |
|---|---|
| `Falta el secret obligatorio SCRAPERS_REPO_TOKEN` | No está cargado o expiró. Ver §3. |
| `Repository not found` en el checkout de scrapers | El PAT no tiene acceso a `Nike_Scrapper_Final`, o expiró, o el repo se renombró. |
| `Ningún CSV nuevo matcheó [...]` | El scraper corrió pero no dejó archivo, o lo dejó con otro nombre. El log lista los CSVs presentes en la carpeta: ajustá el nombre del archivo en el scraper. |
| `Los N CSV encontrados están vacíos (sólo header)` | El sitio bloqueó al scraper o cambió de estructura. No se carga nada — antes esto pasaba silencioso. |
| `Sí existen N CSV viejos ... allow_stale_csv=true` | Sólo hay CSVs de corridas anteriores. Si querés cargarlos igual, disparalo a mano con ese input. |
| `db/load_csv.py falló` | Suele ser `DATABASE_URL` mal formada, la base caída, o el schema desactualizado (`db/schema.sql`). |
| `El loader no insertó ninguna fila` | El CSV tenía filas pero todas fallaron el insert: casi siempre columnas que no matchean el `COL_MAP`. |
| `SMTP_USER/SMTP_PASS no configurados` | El resumen no salió por mail. Está en el Job Summary. No rompe la corrida. |

---

## 9. Verificaciones antes de tocar el workflow

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/scraper.yml'))"
actionlint -shellcheck=shellcheck .github/workflows/scraper.yml
python -m py_compile .github/scripts/*.py scraper/**/*.py
shellcheck .github/scripts/check_secrets.sh
```
