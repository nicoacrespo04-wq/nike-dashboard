"use client";

import type { ReactNode } from "react";
import { num } from "@/lib/format";

// ── Contenedores ────────────────────────────────────────────────────
export function Card({
  children,
  className = "",
  padded = true,
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <section
      className={`rounded-lg border border-line bg-surface-card shadow-card ${padded ? "p-5" : ""} ${className}`}
    >
      {children}
    </section>
  );
}

export function SectionHeader({
  eyebrow,
  title,
  hint,
  right,
}: {
  eyebrow?: string;
  title: string;
  hint?: string;
  right?: ReactNode;
}) {
  return (
    <header className="mb-4 flex items-start justify-between gap-4">
      <div className="min-w-0">
        {eyebrow ? (
          <p className="mb-1 text-2xs font-semibold uppercase tracking-[0.14em] text-nike-red">
            {eyebrow}
          </p>
        ) : null}
        <h2 className="text-base font-semibold leading-tight text-ink-primary">{title}</h2>
        {hint ? <p className="mt-1 text-xs leading-relaxed text-ink-secondary">{hint}</p> : null}
      </div>
      {right ? <div className="shrink-0">{right}</div> : null}
    </header>
  );
}

export function PageHeader({
  question,
  title,
  description,
  right,
}: {
  question: string;
  title: string;
  description: string;
  right?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-col gap-4 border-b border-line pb-5 lg:flex-row lg:items-end lg:justify-between">
      <div className="max-w-3xl">
        <p className="mb-2 inline-flex items-center gap-2 text-2xs font-bold uppercase tracking-[0.16em] text-nike-red">
          <span className="inline-block h-3 w-[3px] rounded-full bg-nike-red" />
          {question}
        </p>
        <h1 className="text-2xl font-bold leading-tight tracking-tight text-ink-primary">{title}</h1>
        <p className="mt-2 text-sm leading-relaxed text-ink-secondary">{description}</p>
      </div>
      {right ? <div className="shrink-0">{right}</div> : null}
    </header>
  );
}

// ── Estados ─────────────────────────────────────────────────────────
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-[#EDEDE9] ${className}`} />;
}

export function LoadingBlock({ rows = 3, label }: { rows?: number; label?: string }) {
  return (
    <div className="space-y-3" role="status" aria-live="polite">
      {label ? <p className="text-xs text-ink-muted">{label}</p> : null}
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  hint,
  icon = "◍",
}: {
  title: string;
  description: string;
  hint?: string;
  icon?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-line-strong bg-surface-sunken px-6 py-10 text-center">
      <span aria-hidden className="mb-3 text-2xl text-ink-muted">
        {icon}
      </span>
      <p className="text-sm font-semibold text-ink-primary">{title}</p>
      <p className="mt-1.5 max-w-md text-xs leading-relaxed text-ink-secondary">{description}</p>
      {hint ? (
        <p className="mt-3 max-w-md rounded border border-line bg-surface-card px-3 py-2 font-mono text-2xs leading-relaxed text-ink-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="rounded-md border border-[#F0C4C4] bg-[#FBECEC] px-4 py-4">
      <p className="text-sm font-semibold text-[#8E2020]">No se pudo cargar la información</p>
      <p className="mt-1 text-xs leading-relaxed text-[#8E2020]/80">{message}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded border border-[#D03B3B] px-3 py-1.5 text-xs font-semibold text-[#8E2020] transition hover:bg-[#D03B3B] hover:text-white"
        >
          Reintentar
        </button>
      ) : null}
    </div>
  );
}

/** Envuelve el patrón cargando / error / vacío / contenido en un solo lugar. */
export function AsyncSection<T>({
  state,
  isEmpty,
  empty,
  children,
  loadingRows = 3,
}: {
  state: { data: T | null; loading: boolean; error: string | null; reload: () => void };
  isEmpty?: (data: T) => boolean;
  empty: ReactNode;
  children: (data: T) => ReactNode;
  loadingRows?: number;
}) {
  if (state.loading && state.data === null) return <LoadingBlock rows={loadingRows} />;
  if (state.error) return <ErrorState message={state.error} onRetry={state.reload} />;
  if (state.data === null) return <>{empty}</>;
  if (isEmpty && isEmpty(state.data)) return <>{empty}</>;
  return <>{children(state.data)}</>;
}

// ── Átomos ──────────────────────────────────────────────────────────
export function Pill({
  children,
  color,
  bg,
  border,
  title,
}: {
  children: ReactNode;
  color?: string;
  bg?: string;
  border?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-2xs font-semibold uppercase tracking-wide"
      style={{
        color: color ?? "#55554F",
        backgroundColor: bg ?? "#F2F2EF",
        borderColor: border ?? "#DEDED9",
      }}
    >
      {children}
    </span>
  );
}

export function Tag({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className="inline-flex items-center rounded border border-line bg-surface-sunken px-1.5 py-0.5 text-2xs font-medium text-ink-secondary"
    >
      {children}
    </span>
  );
}

export function StatTile({
  label,
  value,
  sub,
  accent = false,
  tone,
}: {
  label: string;
  value: number | string;
  sub?: string;
  accent?: boolean;
  tone?: string;
}) {
  return (
    <div
      className={`rounded-lg border bg-surface-card px-4 py-3 shadow-card ${
        accent ? "border-nike-red/30" : "border-line"
      }`}
    >
      <p className="text-2xs font-semibold uppercase tracking-[0.1em] text-ink-muted">{label}</p>
      <p
        className="tabular mt-1 text-2xl font-bold leading-none"
        style={{ color: tone ?? (accent ? "#E31837" : "#111111") }}
      >
        {typeof value === "number" ? num(value) : value}
      </p>
      {sub ? <p className="mt-1.5 text-2xs leading-tight text-ink-secondary">{sub}</p> : null}
    </div>
  );
}

/** Barra horizontal simple con etiqueta directa (nunca color solo). */
export function MeterBar({
  value,
  max = 100,
  color,
  hatched = false,
  height = 8,
}: {
  value: number | null;
  max?: number;
  color: string;
  hatched?: boolean;
  height?: number;
}) {
  const width = value === null || max <= 0 ? 0 : Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div
      className="w-full overflow-hidden rounded-sm bg-[#EDEDE9]"
      style={{ height }}
      role="presentation"
    >
      <div
        className={`h-full rounded-sm ${hatched ? "hatch-muted" : ""}`}
        style={{ width: `${width}%`, backgroundColor: hatched ? undefined : color }}
      />
    </div>
  );
}

export function ScoreBadge({
  value,
  label,
  size = "md",
}: {
  value: number | null | undefined;
  label?: string;
  size?: "sm" | "md" | "lg";
}) {
  const text = value === null || value === undefined ? "—" : value.toFixed(0);
  const sizes = {
    sm: "h-9 w-9 text-xs",
    md: "h-12 w-12 text-sm",
    lg: "h-20 w-20 text-2xl",
  } as const;
  return (
    <div className="flex flex-col items-center gap-1">
      <div
        className={`tabular flex items-center justify-center rounded-full border-2 border-nike-ink font-bold text-nike-ink ${sizes[size]}`}
      >
        {text}
      </div>
      {label ? (
        <span className="text-2xs uppercase tracking-wide text-ink-muted">{label}</span>
      ) : null}
    </div>
  );
}
