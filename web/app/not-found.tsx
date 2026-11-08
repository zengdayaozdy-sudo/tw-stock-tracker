import Link from "next/link";

export default function NotFound() {
  return (
    <>
      <h1>找不到這個頁面</h1>
      <p className="sub">股票代號或族群名稱可能不存在。<Link href="/">回總覽</Link></p>
    </>
  );
}
