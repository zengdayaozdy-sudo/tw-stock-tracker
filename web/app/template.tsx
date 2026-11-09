// 每次切換頁面都會重新掛載：內容依序淡入
export default function Template({ children }: { children: React.ReactNode }) {
  return <div className="page-enter">{children}</div>;
}
