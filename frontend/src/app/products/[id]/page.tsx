"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import type { ProductAttribute, ProductDetail } from "@/types";
import { getProduct, getProductMatches } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { date, dec, money, num, pct, score, text } from "@/lib/format";
import { humanize, scoreTone } from "@/lib/viz";
import { ConfidenceBadge, LifecycleBadge } from "@/components/badges";
import { FactorSparkStack } from "@/components/FactorBreakdown";
import { ProductLine } from "@/components/ProductLine";
import {
  AsyncSection,
  Card,
  EmptyState,
  MeterBar,
  PageHeader,
  SectionHeader,
  StatTile,
  Tag,
} from "@/components/ui";

export default function ProductDetailPage() {
  const params = useParams<{ id: string }>();
  const productId = Number(params?.id ?? 0);

  const state = useApi((signal) => getProduct(productId, signal), [productId], {
    enabled: Number.isFinite(productId) && productId > 0,
  });
  const matchesState = useApi(
    (signal) => getProductMatches(productId, { limit: 5, with_factors: true }, signal),
    [productId],
    { enabled: Number.isFinite(productId) && productId > 0 },
  );

  return (
    <div>
      <Link href="/products" className="mb-3 inline-block text-2xs font-semibold text-nike-red">
        ← Volver al Product Explorer
      </Link>

      <AsyncSection
        state={state}
        loadingRows={5}
        empty={
          <EmptyState
            title="Producto no encontrado"
            description="El id solicitado no existe en el catálogo cargado."
            icon="◌"
          />
        }
      >
        {(product) => (
          <div className="space-y-5">
            <PageHeader
              question={product.is_focus === 1 ? "Producto Nike" : "Producto competidor"}
              title={product.product_name}
              description={
                product.description ??
                "Sin descripción capturada. Los atributos de abajo fueron normalizados e inferidos por el motor de catalogación."
              }
              right={
                <Link
                  href={`/matches?product=${product.id}`}
                  className="inline-block rounded bg-nike-red px-4 py-2 text-xs font-bold text-white transition hover:bg-nike-red-dark"
                >
                  Ver competidores →
                </Link>
              }
            />

            {/* Identidad */}
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              <StatTile label="MSRP" value={money(product.msrp)} sub={text(product.price_band)} />
              <StatTile
                label="Retailers"
                value={product.summary.retailers}
                sub="con observación de precio"
              />
              <StatTile
                label="Precio promedio"
                value={money(product.summary.avg_price)}
                sub={
                  product.summary.min_price !== null
                    ? `${money(product.summary.min_price)} – ${money(product.summary.max_price)}`
                    : "sin observaciones"
                }
              />
              <StatTile
                label="Descuento promedio"
                value={pct(product.summary.avg_discount_pct, 1)}
                sub="último snapshot por retailer"
              />
              <StatTile
                label="Rating"
                value={product.summary.avg_rating !== null ? dec(product.summary.avg_rating, 2) : "—"}
                sub={`${product.reviews.length} review(s)`}
              />
              <StatTile
                label="Ciclo de vida"
                value={text(product.lifecycle_stage)}
                sub={product.launch_date ? `lanzado ${date(product.launch_date)}` : "sin fecha"}
              />
            </div>

            <div className="grid gap-4 xl:grid-cols-[1.1fr_1fr]">
              {/* Taxonomía */}
              <Card>
                <SectionHeader
                  eyebrow="Catalogación"
                  title="Taxonomía normalizada"
                  hint="Los campos que el motor usa para decidir qué compite con qué."
                />
                <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 sm:grid-cols-3">
                  <Field label="Marca" value={product.brand} />
                  <Field label="Franquicia" value={product.franchise} />
                  <Field label="Modelo" value={product.model} />
                  <Field label="Versión" value={product.version} />
                  <Field label="Categoría" value={product.category} />
                  <Field label="Subcategoría" value={product.subcategory} />
                  <Field label="Deporte" value={product.sport} />
                  <Field label="Actividad" value={product.activity} />
                  <Field label="Use case" value={product.use_case} />
                  <Field label="Género" value={product.gender} />
                  <Field label="Segmento etario" value={product.age_segment} />
                  <Field label="Performance / lifestyle" value={product.performance_vs_lifestyle} />
                  <Field label="Segmento de consumidor" value={product.consumer_segment} />
                  <Field label="SKU" value={product.sku} mono />
                  <Field label="Style code" value={product.style_code} mono />
                  <Field label="Nombre normalizado" value={product.normalized_product_name} />
                  <Field label="País" value={product.country_code} />
                </dl>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  <LifecycleBadge stage={product.lifecycle_stage} />
                  {product.price_band ? <Tag title="Banda de precio">{product.price_band}</Tag> : null}
                </div>
              </Card>

              {/* Atributos inferidos */}
              <Card>
                <SectionHeader
                  eyebrow="Why?"
                  title="Atributos enriquecidos"
                  hint="Cada atributo declara de dónde salió y con cuánta confianza fue inferido."
                />
                <AttributeGroups attributes={product.attributes} />
              </Card>
            </div>

            {/* Competidores */}
            <Card>
              <SectionHeader
                eyebrow="Who is competing?"
                title="Top competidores"
                hint="Ranking del motor competitivo. Cada barra muestra cómo se repartió el score entre los factores disponibles."
                right={
                  <Link href={`/matches?product=${product.id}`} className="text-2xs font-semibold text-nike-red">
                    Ver ranking completo →
                  </Link>
                }
              />
              <AsyncSection
                state={matchesState}
                loadingRows={3}
                isEmpty={(d) => d.matches.length === 0}
                empty={
                  <EmptyState
                    title="Sin competidores calculados"
                    description="Este producto no tiene matches persistidos. Puede ser que el motor de matching aún no haya corrido, o que ningún candidato superara el score mínimo configurado."
                    icon="⇄"
                  />
                }
              >
                {(data) => (
                  <ul className="divide-y divide-line">
                    {data.matches.map((m, i) => (
                      <li key={m.id} className="py-2.5 first:pt-0">
                        <Link href={`/matches/${m.id}`} className="block">
                          <div className="flex items-center gap-3">
                            <span className="tabular w-5 shrink-0 text-center text-xs font-bold text-ink-muted">
                              {i + 1}
                            </span>
                            <span className="min-w-0 flex-1">
                              <ProductLine product={m.competitor} role="competitor" link={false} compact />
                            </span>
                            <span className="w-36 shrink-0 text-right">
                              <span
                                className="tabular block text-base font-bold"
                                style={{ color: scoreTone(m.match_score) }}
                              >
                                {score(m.match_score)}%
                              </span>
                              <ConfidenceBadge confidence={m.confidence} coverage={m.coverage} />
                            </span>
                          </div>
                          <div className="mt-1.5 pl-8">
                            <FactorSparkStack factors={m.factors} />
                          </div>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </AsyncSection>
            </Card>

            {/* Precio y stock */}
            <div className="grid gap-4 xl:grid-cols-2">
              <Card>
                <SectionHeader title="Precio por retailer" hint="Observaciones más recientes primero." />
                {product.prices.length === 0 ? (
                  <EmptyState
                    title="Sin observaciones de precio"
                    description="No hay filas en price_observations para este producto."
                    icon="₳"
                  />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-line text-left text-2xs uppercase tracking-wide text-ink-muted">
                          <th className="py-1.5 pr-3 font-semibold">Retailer</th>
                          <th className="py-1.5 pr-3 font-semibold">Fecha</th>
                          <th className="py-1.5 pr-3 text-right font-semibold">Full</th>
                          <th className="py-1.5 pr-3 text-right font-semibold">Actual</th>
                          <th className="py-1.5 text-right font-semibold">Desc.</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line">
                        {product.prices.slice(0, 12).map((p) => (
                          <tr key={p.id}>
                            <td className="py-1.5 pr-3 text-ink-primary">{text(p.retailer_name)}</td>
                            <td className="py-1.5 pr-3 text-ink-muted">{date(p.observed_at)}</td>
                            <td className="tabular py-1.5 pr-3 text-right text-ink-secondary">
                              {money(p.full_price, p.currency ?? "ARS")}
                            </td>
                            <td className="tabular py-1.5 pr-3 text-right font-semibold text-ink-primary">
                              {money(p.current_price, p.currency ?? "ARS")}
                            </td>
                            <td className="tabular py-1.5 text-right text-ink-secondary">
                              {pct(p.discount_pct, 0)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              <Card>
                <SectionHeader title="Disponibilidad" hint="Cobertura de talles por retailer." />
                {product.stock.length === 0 ? (
                  <EmptyState
                    title="Sin observaciones de stock"
                    description="No hay filas en stock_observations para este producto."
                    icon="▦"
                  />
                ) : (
                  <ul className="space-y-2">
                    {product.stock.slice(0, 10).map((s) => (
                      <li key={s.id} className="grid grid-cols-[1fr_auto] items-center gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-xs font-medium text-ink-primary">
                            {text(s.retailer_name)}
                          </p>
                          <MeterBar
                            value={s.availability_pct}
                            max={100}
                            color={(s.availability_pct ?? 0) >= 60 ? "#0CA30C" : "#FAB219"}
                            height={6}
                          />
                        </div>
                        <div className="text-right">
                          <span className="tabular text-xs font-bold text-ink-primary">
                            {pct(s.availability_pct, 0)}
                          </span>
                          <p className="text-2xs text-ink-muted">
                            {num(s.sizes_available)}/{num(s.sizes_total)} talles
                          </p>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          </div>
        )}
      </AsyncSection>
    </div>
  );
}

function Field({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string | null | undefined;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-2xs uppercase tracking-wide text-ink-muted">{label}</dt>
      <dd className={`text-xs font-medium text-ink-primary ${mono ? "font-mono" : ""}`}>
        {text(value)}
      </dd>
    </div>
  );
}

function AttributeGroups({ attributes }: { attributes: ProductDetail["attributes"] }) {
  const groups = Object.entries(attributes);
  if (groups.length === 0) {
    return (
      <EmptyState
        title="Sin atributos enriquecidos"
        description="La tabla product_attributes está vacía para este producto. La etapa de enrichment es la que infiere silueta, materiales, colores, drop, etc."
        icon="◇"
      />
    );
  }

  return (
    <div className="space-y-4">
      {groups.map(([group, attrs]) => (
        <div key={group}>
          <p className="mb-1.5 text-2xs font-semibold uppercase tracking-[0.1em] text-ink-muted">
            {humanize(group)}
          </p>
          <ul className="space-y-1.5">
            {attrs.map((a) => (
              <AttributeRow key={a.attr_name} attribute={a} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function AttributeRow({ attribute }: { attribute: ProductAttribute }) {
  const confidence = attribute.confidence ?? 1;
  const inferred = attribute.source !== "scraper" && attribute.source !== "manual";
  const value =
    attribute.value_text ?? (attribute.value_num !== null ? dec(attribute.value_num, 2) : "—");

  return (
    <li className="grid grid-cols-[minmax(90px,1fr)_minmax(70px,1fr)_auto] items-center gap-2">
      <span className="truncate text-2xs text-ink-secondary" title={attribute.attr_name}>
        {humanize(attribute.attr_name)}
      </span>
      <span className="truncate text-xs font-medium text-ink-primary" title={value}>
        {value}
      </span>
      <span className="flex shrink-0 items-center gap-1.5">
        <span
          className="tabular w-9 text-right text-2xs font-semibold"
          style={{ color: confidence >= 0.75 ? "#0A6B0A" : confidence >= 0.5 ? "#7A5600" : "#8A8A83" }}
          title={`Confianza de la inferencia: ${(confidence * 100).toFixed(0)}%`}
        >
          {(confidence * 100).toFixed(0)}%
        </span>
        <span
          className="rounded border border-line bg-surface-sunken px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-ink-muted"
          title={inferred ? "Valor inferido por el motor" : "Valor capturado del origen"}
        >
          {inferred ? `inf · ${text(attribute.source)}` : text(attribute.source)}
        </span>
      </span>
    </li>
  );
}
