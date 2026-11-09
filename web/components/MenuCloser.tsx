"use client";
// 點選頁面其他地方或按 Esc 時，收起已展開的選單（details.menu、details.reveal）。
import { useEffect } from "react";

export function MenuCloser() {
  useEffect(() => {
    const closeAll = (except?: Node) => {
      document.querySelectorAll<HTMLDetailsElement>("details.menu[open], details.reveal[open]:not(.inline-reveal)").forEach((d) => {
        if (!except || !d.contains(except)) d.open = false;
      });
    };
    const onClick = (e: MouseEvent) => closeAll(e.target as Node);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeAll();
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, []);
  return null;
}
