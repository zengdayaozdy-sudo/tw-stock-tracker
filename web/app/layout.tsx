import type { Metadata, Viewport } from "next";
import { MenuCloser } from "@/components/MenuCloser";
import { Nav } from "@/components/Nav";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/ibm-plex-mono/600.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "台股追蹤",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

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
