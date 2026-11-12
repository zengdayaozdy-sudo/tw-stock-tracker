import type { MetadataRoute } from "next";

// 加到手機主畫面時的名稱與圖示（只供本人使用，不影響登入保護）
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "台股追蹤",
    short_name: "台股追蹤",
    start_url: "/",
    display: "standalone",
    background_color: "#f4f3ef",
    theme_color: "#15202e",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
