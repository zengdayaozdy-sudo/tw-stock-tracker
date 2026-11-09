// 換頁等待資料時的骨架畫面
export default function Loading() {
  return (
    <div className="skeleton-page" aria-busy="true" aria-label="載入中">
      <div className="sk sk-title" />
      <div className="sk sk-sub" />
      <div className="kpis">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="kpi">
            <div className="sk sk-line short" />
            <div className="sk sk-value" />
          </div>
        ))}
      </div>
      <div className="grid two" style={{ marginTop: 14 }}>
        {[0, 1].map((i) => (
          <div key={i} className="panel">
            <div className="panel-head">
              <div className="sk sk-line short" />
            </div>
            <div className="panel-body">
              {[0, 1, 2, 3, 4].map((j) => (
                <div key={j} className="sk sk-line" />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
