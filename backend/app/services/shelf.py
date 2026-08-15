"""Share of Shelf: participación de Nike en el surtido de cada retailer.

Faltaba una pieza en la cadena: `opportunities._rule_share_of_shelf_risk` y el
componente `share_of_shelf` de Business Importance consumen señales
``market_signals(signal_type='share_of_shelf')``, pero ningún módulo las
producía. Este servicio las calcula.

Definición: para cada retailer y cada fecha de captura, el share of shelf de
Nike es el porcentaje de SKUs distintos presentes en ese canal que son Nike.
"Presente" = observado en precio o en stock esa fecha, así un producto
listado sin precio no desaparece del cálculo.

La serie temporal da `delta` (variación en puntos porcentuales contra la
captura anterior) y `acceleration` (variación de la variación), que es lo que
distingue "Nike tiene poco share" de "Nike está perdiendo share".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import DB_PATH
from app.db import get_conn, query

SIGNAL_TYPE = "share_of_shelf"

# Presencia en góndola = visto en precio o en stock esa fecha.
PRESENCE_SQL = """
SELECT observed_at, retailer_id, product_id FROM price_observations
UNION
SELECT observed_at, retailer_id, product_id FROM stock_observations
"""


def _presence(db_path: Path | str) -> list[dict[str, Any]]:
    return query(
        f"""
        SELECT pres.observed_at, pres.retailer_id, pres.product_id,
               b.is_focus, r.country_code
        FROM ({PRESENCE_SQL}) AS pres
        JOIN products p  ON p.id = pres.product_id
        JOIN brands   b  ON b.id = p.brand_id
        JOIN retailers r ON r.id = pres.retailer_id
        """,
        path=db_path,
    )


def compute_shelf_series(db_path: Path | str = DB_PATH) -> dict[int, list[dict[str, Any]]]:
    """Serie de share of shelf por retailer, ordenada por fecha."""
    by_retailer: dict[int, dict[str, dict[str, set[int] | str]]] = {}

    for row in _presence(db_path):
        rid = int(row["retailer_id"])
        when = str(row["observed_at"])
        bucket = by_retailer.setdefault(rid, {}).setdefault(
            when, {"nike": set(), "total": set(), "country": row.get("country_code")})
        bucket["total"].add(int(row["product_id"]))  # type: ignore[union-attr]
        if row.get("is_focus"):
            bucket["nike"].add(int(row["product_id"]))  # type: ignore[union-attr]

    series: dict[int, list[dict[str, Any]]] = {}
    for rid, periods in by_retailer.items():
        points = []
        for when in sorted(periods):
            bucket = periods[when]
            total = len(bucket["total"])  # type: ignore[arg-type]
            nike = len(bucket["nike"])    # type: ignore[arg-type]
            if total == 0:
                continue
            points.append({
                "observed_at": when,
                "share_pct": round(100.0 * nike / total, 3),
                "nike_skus": nike,
                "total_skus": total,
                "country_code": bucket["country"],
            })
        if points:
            series[rid] = points
    return series


def run_shelf_signals(db_path: Path | str = DB_PATH) -> dict[str, int]:
    """Persiste las señales de share of shelf. Idempotente."""
    series = compute_shelf_series(db_path)

    rows: list[tuple] = []
    for rid, points in series.items():
        latest = points[-1]
        previous = points[-2] if len(points) >= 2 else None
        earlier = points[-3] if len(points) >= 3 else None

        delta = None
        if previous is not None:
            delta = round(latest["share_pct"] - previous["share_pct"], 3)

        acceleration = None
        if previous is not None and earlier is not None:
            prev_delta = previous["share_pct"] - earlier["share_pct"]
            acceleration = round((latest["share_pct"] - previous["share_pct"]) - prev_delta, 3)

        rows.append((
            SIGNAL_TYPE, "retailer", str(rid), latest["country_code"],
            latest["share_pct"], delta, acceleration,
            points[0]["observed_at"], latest["observed_at"],
        ))

    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM market_signals WHERE signal_type = ?", (SIGNAL_TYPE,))
        conn.executemany(
            "INSERT INTO market_signals (signal_type, entity_type, entity_id, country_code, "
            "value, delta, acceleration, period_start, period_end) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )

    dropping = sum(1 for r in rows if r[5] is not None and r[5] < 0)
    return {"retailers": len(series), "signals": len(rows), "retailers_losing_shelf": dropping}
