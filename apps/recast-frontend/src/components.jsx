export function Logo() {
  return (
    <div className="brand">
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none">
        <rect x="4" y="3" width="16" height="18" rx="2" stroke="#14171f" strokeWidth="2" />
        <rect x="7" y="6" width="3" height="3" fill="#14171f" />
        <rect x="14" y="6" width="3" height="3" fill="#14171f" />
        <rect x="7" y="11" width="3" height="3" fill="#14171f" />
        <rect x="14" y="11" width="3" height="3" fill="#14171f" />
        <rect x="10" y="16" width="4" height="5" fill="#14171f" />
      </svg>
      Recast
    </div>
  );
}

export function ProgressBar({ step, total = 6 }) {
  return (
    <div className="progress-track">
      {Array.from({ length: total }, (_, i) => (
        <span key={i} className={i < step ? 'done' : i === step ? 'current' : ''} />
      ))}
    </div>
  );
}

export function Pill({ tone = 'blue', children, onClick }) {
  return (
    <button className={`pill ${tone} ${onClick ? 'clickable' : ''}`} onClick={onClick} type="button">
      {children}
    </button>
  );
}

export function scoreColor(bhi) {
  if (bhi === null || bhi === undefined) return '#9aa3b0';
  if (bhi < 20) return '#c0392b';
  if (bhi < 40) return '#e2712b';
  if (bhi < 60) return '#d9a51b';
  if (bhi < 80) return '#2f9e4f';
  return '#14b866';
}

export function evidenceTierNote(coverage) {
  if (coverage >= 0.8) return { label: 'Strong evidence', tone: 'green' };
  if (coverage >= 0.4) return { label: 'Partial evidence', tone: 'blue' };
  return { label: 'Insufficient evidence', tone: 'neutral' };
}
