import type { Metadata } from 'next'
import './globals.css'
import SessionProvider from '@/components/ui/SessionProvider'

export const metadata: Metadata = {
  title: 'Nike Analytics Dashboard',
  description: 'Competitive intelligence platform for Nike Argentina',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  )
}
