"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { getHealth } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { num } from "@/lib/format";

interface NavItem {
  href: string;
  label: string;
  question: string;
}

/** El orden del nav ES el flujo de decisión: qué pasa → quién compite → cuánto importa → qué hacer. */
const NAV: NavItem[] = [
  { href: "/", label: "Executive Overview", question: "What is happening?" },
  { href: "/products", label: "Product Explorer", question: "What is happening?" },
  { href: "/matches", label: "Competitive Matches", question: "Who is competing?" },
  { href: "/opportunities", label: "Opportunity Center", question: "Does it matter? Why?" },
  { href: "/retail-media", label: "Retail Media", question: "What should we do?" },
  { href: "/brand", label: "Consumer & Brand AR", question: "What is happening?" },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** Cinta de estado del pipeline: qué tablas tienen datos y cuáles todavía no. */
function PipelineStrip() {
  const health = useApi((signal) => getHealth(signal), []);

  if (health.error) {
    return (
      <div className="border-b border-[#F0C4C4] bg-[#FBECEC] px-6 py-1.5 text-2xs text-[#8E2020]">
        Backend inaccesible — {health.error}
      </div>
    );
  }

  const data = health.data;
  if (!data) {
    return <div className="h-[26px] border-b border-line bg-surface-sunken" />;
  }

  const total = Object.keys(data.tables).length;
  const filled = total - data.empty_tables.length;
  const rows = Object.values(data.tables).reduce((acc, n) => acc + n, 0);
  const complete = data.empty_tables.length === 0;

  return (
    <div
      className={`flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-6 py-1.5 text-2xs ${
        complete
          ? "border-line bg-surface-sunken text-ink-secondary"
          : "border-[#F6E0AE] bg-[#FEF6E4] text-[#7A5600]"
      }`}
    >
      <span className="font-semibold">
        Pipeline: {filled}/{total} tablas con datos · {num(rows)} registros
      </span>
      {!complete ? (
        <span className="truncate">
          Etapas pendientes → sin datos:{" "}
          <span className="font-mono">{data.empty_tables.join(", ")}</span>
        </span>
      ) : (
        <span>Todas las etapas del pipeline poblaron su tabla.</span>
      )}
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";

  return (
    <div className="min-h-screen bg-surface-page">
      <header className="sticky top-0 z-30 border-b border-line bg-surface-card/95 backdrop-blur">
        <div className="flex items-center gap-6 px-6 py-3">
          <Link href="/" className="flex items-center gap-3">
            <span aria-hidden className="block h-6 w-1.5 rounded-full bg-nike-red" />
            <span className="leading-tight">
              <span className="block text-sm font-bold tracking-tight text-nike-ink">
                Competitive &amp; Consumer Intelligence
              </span>
              <span className="block text-2xs uppercase tracking-[0.16em] text-ink-muted">
                Decision Engine · Nike Argentina
              </span>
            </span>
          </Link>
        </div>
        <nav className="flex gap-0 overflow-x-auto px-6" aria-label="Secciones">
          {NAV.map((item) => {
            const active = isActive(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`shrink-0 border-b-2 px-3 py-2 text-xs font-semibold transition ${
                  active
                    ? "border-nike-red text-nike-ink"
                    : "border-transparent text-ink-secondary hover:border-line-strong hover:text-ink-primary"
                }`}
                title={item.question}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </header>

      <PipelineStrip />

      <main className="mx-auto max-w-[1500px] px-6 py-6">{children}</main>

      <footer className="mx-auto max-w-[1500px] px-6 pb-10 pt-4 text-2xs leading-relaxed text-ink-muted">
        Todos los scores son explicables y se calculan con los pesos vigentes de{" "}
        <span className="font-mono">config/weights.yaml</span>. Un factor sin datos no penaliza: se
        excluye y se renormaliza. Señales sociales siempre agregadas, nunca individuales.
      </footer>
    </div>
  );
}
