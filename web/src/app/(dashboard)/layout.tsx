'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { signOut, useSession } from 'next-auth/react'
import {
  BarChart2,
  Eye,
  ShieldCheck,
  Layers,
  LogOut,
  ChevronRight,
} from 'lucide-react'
import NikeSwoosh from '@/components/ui/NikeSwoosh'

const NAV = [
  {
    href:  '/competencia',
    label: 'Competencia',
    sub:   'Adidas & Puma',
    icon:  BarChart2,
  },
  {
    href:  '/shelf',
    label: 'Share of Shelf',
    sub:   'Visibilidad en retailers',
    icon:  Eye,
  },
  {
    href:  '/control-retailers',
    label: 'Control Retailers',
    sub:   'PVP · Markdown · BML',
    icon:  ShieldCheck,
  },
  {
    href:  '/assortment',
    label: 'Assortment',
    sub:   'Omnicanalidad vs competencia',
    icon:  Layers,
  },
]

const SECTION_TITLES: Record<string, string> = {
  '/competencia':      'Competencia — Adidas & Puma',
  '/shelf':            'Share of Shelf',
  '/control-retailers':'Control de Retailers',
  '/assortment':       'Assortment & Omnicanalidad',
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const { data: session } = useSession()

  const title = Object.entries(SECTION_TITLES).find(([k]) => pathname.startsWith(k))?.[1] ?? 'Dashboard'

  return (
    <div className="flex h-screen bg-[#F5F5F5] overflow-hidden">
      {/* ── Sidebar ── */}
      <aside className="w-64 flex-shrink-0 bg-[#111111] flex flex-col h-full">
        {/* Logo */}
        <div className="px-6 py-6 border-b border-white/10">
          <NikeSwoosh width={56} height={21} color="#FFFFFF" />
          <p className="text-white/40 text-[10px] font-semibold tracking-[0.25em] uppercase mt-2">
            Analytics
          </p>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-3 py-5 space-y-1 overflow-y-auto">
          {NAV.map(({ href, label, sub, icon: Icon }) => {
            const active = pathname.startsWith(href)
            return (
              <Link
                key={href}
                href={href}
                className={`
                  flex items-center gap-3 px-3 py-3 rounded-xl transition-all group
                  ${active
                    ? 'bg-[#E31837] text-white'
                    : 'text-white/50 hover:text-white hover:bg-white/8'}
                `}
              >
                <Icon size={18} className="flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold leading-tight truncate">{label}</div>
                  <div className={`text-[10px] leading-tight truncate mt-0.5 ${active ? 'text-white/70' : 'text-white/30'}`}>
                    {sub}
                  </div>
                </div>
                {active && <ChevronRight size={14} className="flex-shrink-0 opacity-60" />}
              </Link>
            )
          })}
        </nav>

        {/* Footer */}
        <div className="px-3 py-4 border-t border-white/10">
          <div className="flex items-center gap-3 px-3 py-2 mb-1">
            <div className="w-7 h-7 rounded-full bg-[#E31837] flex items-center justify-center text-white text-xs font-bold flex-shrink-0">
              N
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-white text-xs font-semibold truncate">Nike Argentina</div>
              <div className="text-white/30 text-[10px] truncate">{session?.user?.email ?? ''}</div>
            </div>
          </div>
          <button
            onClick={() => signOut({ callbackUrl: '/login' })}
            className="flex items-center gap-2 w-full px-3 py-2 text-white/40 hover:text-white hover:bg-white/8 rounded-lg transition-all text-xs"
          >
            <LogOut size={14} />
            Cerrar sesión
          </button>
        </div>
      </aside>

      {/* ── Main ── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar */}
        <header className="bg-white border-b border-gray-200 px-8 py-4 flex items-center justify-between flex-shrink-0">
          <div>
            <h1 className="text-[#111111] text-lg font-bold leading-tight">{title}</h1>
            <p className="text-gray-400 text-xs mt-0.5">
              Última actualización: 10 Aug 2026 · Season FA26
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 bg-green-50 text-green-700 border border-green-200 rounded-full px-3 py-1 text-xs font-semibold">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
              Live
            </span>
          </div>
        </header>

        {/* Content */}
        <main className="flex-1 overflow-y-auto px-8 py-6">
          {children}
        </main>
      </div>
    </div>
  )
}
