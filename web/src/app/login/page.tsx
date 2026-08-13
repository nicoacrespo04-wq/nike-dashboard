'use client'

import { signIn } from 'next-auth/react'
import { useState } from 'react'
import { useRouter } from 'next/navigation'

export default function LoginPage() {
  const router = useRouter()
  const [email, setEmail]       = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError('')
    const res = await signIn('credentials', {
      email,
      password,
      redirect: false,
    })
    setLoading(false)
    if (res?.ok) {
      router.push('/competencia')
    } else {
      setError('Email o contraseña incorrectos')
    }
  }

  return (
    <div className="min-h-screen bg-[#111111] flex flex-col items-center justify-center px-4">
      {/* Swoosh + wordmark */}
      <div className="mb-10 flex flex-col items-center gap-2">
        <svg width="80" height="30" viewBox="0 0 80 30" fill="none" aria-label="Nike swoosh">
          <path
            d="M8.027 22.911L24.234 3.493c.734-.874 1.836-1.38 3.009-1.38h42.49c.617 0 1.02.662.734 1.209L54.65 22.204c-.734 1.38-2.139 2.254-3.67 2.254H8.551c-.794 0-1.256-.92-.524-1.547z"
            fill="white"
          />
        </svg>
        <span className="text-white text-xs font-semibold tracking-[0.3em] uppercase opacity-60">
          Analytics Dashboard
        </span>
      </div>

      {/* Card */}
      <div className="w-full max-w-sm bg-[#1a1a1a] rounded-2xl p-8 border border-white/10 shadow-2xl">
        <h1 className="text-white text-xl font-bold mb-1">Iniciar sesión</h1>
        <p className="text-gray-400 text-sm mb-7">Acceso exclusivo Nike Argentina</p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label className="text-gray-400 text-xs font-semibold uppercase tracking-wider block mb-1.5">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="nike@southbay.com.ar"
              required
              className="w-full bg-[#222] border border-white/10 rounded-lg px-4 py-3 text-white text-sm placeholder-gray-600 focus:outline-none focus:border-[#E31837] transition-colors"
            />
          </div>

          <div>
            <label className="text-gray-400 text-xs font-semibold uppercase tracking-wider block mb-1.5">
              Contraseña
            </label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="••••••••"
              required
              className="w-full bg-[#222] border border-white/10 rounded-lg px-4 py-3 text-white text-sm placeholder-gray-600 focus:outline-none focus:border-[#E31837] transition-colors"
            />
          </div>

          {error && (
            <div className="bg-red-900/30 border border-red-500/30 rounded-lg px-4 py-3 text-red-400 text-sm">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-[#E31837] hover:bg-[#c41530] disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold py-3 rounded-lg transition-colors text-sm tracking-wide mt-1"
          >
            {loading ? 'Ingresando...' : 'Ingresar'}
          </button>
        </form>
      </div>

      <p className="text-gray-600 text-xs mt-8">
        © {new Date().getFullYear()} Southbay · Powered by Scout
      </p>
    </div>
  )
}
