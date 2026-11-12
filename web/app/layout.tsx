import type { Metadata, Viewport } from "next";
import { MenuCloser } from "@/components/MenuCloser";
import { Nav } from "@/components/Nav";
import "@fontsource-variable/inter";
import "./globals.css";

export const metadata: Metadata = {
  title: "台股追蹤",
  robots: { index: false, follow: false },
  appleWebApp: { capable: true, title: "台股追蹤", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f3ef" },
    { media: "(prefers-color-scheme: dark)", color: "#0e0f11" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-Hant-TW">
      <body>
        <Nav />
        <div className="shell">{children}</div>
        <MenuCloser />
      </body>
    </html>
  );
}
