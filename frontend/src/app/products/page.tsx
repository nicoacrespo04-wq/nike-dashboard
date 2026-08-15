"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { Product } from "@/types";
import { getProductFilters, getProducts, type ProductQuery } from "@/lib/api";
import { useApi, useDebounced } from "@/lib/useApi";
import { money, num, text } from "@/lib/format";
import { LifecycleBadge } from "@/components/badges";
import {
  AsyncSection,
  Card,
  EmptyState,
  LoadingBlock,
  PageHeader,
  Tag,
} from "@/components/ui";

const PAGE_SIZE = 40;

interface FilterState {
  brand: string;
  franchise: string;
  category: string;
  sport: string;
  use_case: string;
  gender: string;
  price_band: string;
  country: string;
  retailer: string;
}

const EMPTY_FILTERS: FilterState = {
  brand: "",
  franchise: "",
  category: "",
  sport: "",
  use_case: "",
  gender: "",
  price_band: "",
  country: "",
  retailer: "",
};

export default function ProductsPage() {
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const debouncedQ = useDebounced(q, 300);

  const filtersState = useApi((signal) => getProductFilters(signal), []);

  const query: ProductQuery = useMemo(
    () => ({
      brand: filters.brand || undefined,
      franchise: filters.franchise || undefined,
      category: filters.category || undefined,
      sport: filters.sport || undefined,
      use_case: filters.use_case || undefined,
      gender: filters.gender || undefined,
      price_band: filters.price_band || undefined,
      country: filters.country || undefined,
      retailer: filters.retailer ? Number(filters.retailer) : undefined,
      q: debouncedQ || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }),
    [filters, debouncedQ, page],
  );

  const productsState = useApi((signal) => getProducts(query, signal), [JSON.stringify(query)]);

  const set = (key: keyof FilterState, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(0);
  };

  const activeCount =
    Object.values(filters).filter((v) => v !== "").length + (debouncedQ ? 1 : 0);

  return (
    <div>
      <PageHeader
        question="What is happening?"
        title="Product Explorer"
        description="El catálogo normalizado y enriquecido: taxonomía inferida, banda de precio, ciclo de vida y atributos con su nivel de confianza. La catalogación es parte del valor, no un paso invisible."
      />

      {/* ── Filtros ─────────────────────────────────────────────── */}
      <Card className="mb-4">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <label className="block xl:col-span-2">
            <span className="mb-1 block text-2xs font-semibold uppercase tracking-wide text-ink-muted">
              Búsqueda libre (nombre, franquicia, SKU, style code)
            </span>
            <input
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setPage(0);
              }}
              placeholder="Pegasus, Novablast, DV3853…"
              className="w-full rounded border border-line bg-surface-card px-3 py-2 text-sm text-ink-primary placeholder:text-ink-muted focus:border-nike-red focus:outline-none"
            />
          </label>

          {filtersState.loading ? (
            <div className="xl:col-span-2">
              <LoadingBlock rows={1} />
            </div>
          ) : null}
        </div>

        {filtersState.data ? (
          <div className="mt-3 grid gap-3 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4">
            <Select
              label="Marca"
              value={filters.brand}
              options={filtersState.data.brands}
              onChange={(v) => set("brand", v)}
            />
            <Select
              label="Franquicia"
              value={filters.franchise}
              options={filtersState.data.franchises}
              onChange={(v) => set("franchise", v)}
            />
            <Select
              label="Categoría"
              value={filters.category}
              options={filtersState.data.categories}
              onChange={(v) => set("category", v)}
            />
            <Select
              label="Deporte"
              value={filters.sport}
              options={filtersState.data.sports}
              onChange={(v) => set("sport", v)}
            />
            <Select
              label="Use case"
              value={filters.use_case}
              options={filtersState.data.use_cases}
              onChange={(v) => set("use_case", v)}
            />
            <Select
              label="Género"
              value={filters.gender}
              options={filtersState.data.genders}
              onChange={(v) => set("gender", v)}
            />
            <Select
              label="Banda de precio"
              value={filters.price_band}
              options={filtersState.data.price_bands}
              onChange={(v) => set("price_band", v)}
            />
            <Select
              label="País"
              value={filters.country}
              options={filtersState.data.countries}
              onChange={(v) => set("country", v)}
            />
            <Select
              label="Retailer"
              value={filters.retailer}
              options={filtersState.data.retailers.map((r) => ({
                value: String(r.id),
                label: r.name,
              }))}
              onChange={(v) => set("retailer", v)}
            />
          </div>
        ) : null}

        {activeCount > 0 ? (
          <div className="mt-3 flex items-center gap-3 border-t border-line pt-3">
            <span className="text-2xs text-ink-secondary">{activeCount} filtro(s) activo(s)</span>
            <button
              type="button"
              onClick={() => {
                setFilters(EMPTY_FILTERS);
                setQ("");
                setPage(0);
              }}
              className="text-2xs font-semibold text-nike-red hover:underline"
            >
              Limpiar todo
            </button>
          </div>
        ) : null}
      </Card>

      {/* ── Resultados ──────────────────────────────────────────── */}
      <AsyncSection
        state={productsState}
        loadingRows={8}
        empty={
          <Card>
            <EmptyState
              title="Sin productos"
              description="La tabla products está vacía. La etapa de seed/enrichment del pipeline todavía no cargó el catálogo."
              hint="cd backend && python -m app.pipeline"
            />
          </Card>
        }
        isEmpty={(d) => d.items.length === 0}
      >
        {(data) => (
          <div>
            <div className="mb-3 flex items-center justify-between">
              <p className="text-xs text-ink-secondary">
                <span className="tabular font-bold text-ink-primary">{num(data.total)}</span>{" "}
                producto(s) · mostrando {data.offset + 1}–
                {Math.min(data.offset + data.items.length, data.total)}
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={page === 0}
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  className="rounded border border-line px-3 py-1 text-2xs font-semibold text-ink-secondary disabled:opacity-40"
                >
                  ← Anterior
                </button>
                <button
                  type="button"
                  disabled={data.offset + data.items.length >= data.total}
                  onClick={() => setPage((p) => p + 1)}
                  className="rounded border border-line px-3 py-1 text-2xs font-semibold text-ink-secondary disabled:opacity-40"
                >
                  Siguiente →
                </button>
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
              {data.items.map((p) => (
                <ProductGridCard key={p.id} product={p} />
              ))}
            </div>
          </div>
        )}
      </AsyncSection>
    </div>
  );
}

type Option = string | { value: string; label: string };

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: Option[];
  onChange: (value: string) => void;
}) {
  const normalized = options.map((o) => (typeof o === "string" ? { value: o, label: o } : o));
  return (
    <label className="block">
      <span className="mb-1 block text-2xs font-semibold uppercase tracking-wide text-ink-muted">
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={normalized.length === 0}
        className="w-full rounded border border-line bg-surface-card px-2 py-1.5 text-xs text-ink-primary focus:border-nike-red focus:outline-none disabled:bg-surface-sunken disabled:text-ink-muted"
      >
        <option value="">
          {normalized.length === 0 ? "sin valores" : `Todos (${normalized.length})`}
        </option>
        {normalized.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function ProductGridCard({ product }: { product: Product }) {
  const isNike = product.is_focus === 1;
  return (
    <Link
      href={`/products/${product.id}`}
      className="flex flex-col rounded-lg border bg-surface-card p-3 shadow-card transition hover:border-line-strong hover:shadow-pop"
      style={{ borderColor: isNike ? "rgba(227,24,55,0.35)" : "#E3E3DF" }}
    >
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span
          className={`text-2xs font-bold uppercase tracking-wide ${
            isNike ? "text-nike-red" : "text-ink-muted"
          }`}
        >
          {text(product.brand)}
        </span>
        {product.country_code ? <Tag>{product.country_code}</Tag> : null}
      </div>
      <p className="text-sm font-semibold leading-snug text-ink-primary">{product.product_name}</p>
      <p className="mt-0.5 text-2xs text-ink-muted">
        {[product.franchise, product.model, product.version].filter(Boolean).join(" · ") || "—"}
      </p>

      <div className="mt-2 flex flex-wrap gap-1">
        {product.category ? <Tag title="Categoría">{product.category}</Tag> : null}
        {product.use_case ? <Tag title="Use case">{product.use_case}</Tag> : null}
        {product.gender ? <Tag title="Género">{product.gender}</Tag> : null}
        {product.performance_vs_lifestyle ? (
          <Tag title="Performance vs lifestyle">{product.performance_vs_lifestyle}</Tag>
        ) : null}
      </div>

      <div className="mt-auto flex items-end justify-between gap-2 pt-3">
        <div>
          <p className="tabular text-sm font-bold text-ink-primary">{money(product.msrp)}</p>
          <p className="text-2xs text-ink-muted">{text(product.price_band)}</p>
        </div>
        <LifecycleBadge stage={product.lifecycle_stage} />
      </div>
    </Link>
  );
}
