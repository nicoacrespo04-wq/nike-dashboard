import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: "Competitive & Consumer Intelligence · Nike Argentina",
  description:
    "Motor de decisión competitiva y de consumidor: qué pasa, quién compite, cuánto importa, por qué y qué hacer.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
