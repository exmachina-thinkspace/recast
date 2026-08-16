export function Logo() {
  return (
    <div className="brand">
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none">
        <path d="M5 20V6l7-3 7 3v14" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
        <path d="m5 6 7 3 7-3M12 9v12M3 21h18" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        <path d="M8 10.8v2M8 15.5v2M16 10.8v2M16 15.5v2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      <span>RECAST</span>
      <i aria-hidden="true" />
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

export function ReuseImagePanel({
  result,
  sourcePreview,
  busy = false,
  error,
  proposedUse,
  onGenerate,
  disabled = false,
}) {
  const visual = result?.imageUrl || sourcePreview;
  return (
    <div className={`future-image-panel ${result ? 'has-result' : ''}`}>
      <div className="future-image-stage">
        {visual ? (
          <img
            src={visual}
            alt={result ? `Concept visualization: ${proposedUse}` : 'Uploaded room reference'}
          />
        ) : (
          <div className="future-image-empty" aria-hidden="true"><i /><i /><span>IMAGE / FUTURE</span></div>
        )}
        <div className="future-image-hud">
          <span>{result ? 'GENERATED FUTURE' : sourcePreview ? 'SOURCE SPACE' : 'VISUAL PIPELINE'}</span>
          <span>{result?.mode === 'depth' ? 'STRUCTURE / PRESERVED' : result ? 'CONCEPT / BASE' : 'STANDBY'}</span>
        </div>
        {busy && <div className="future-image-loading"><span className="scanner" /> Rendering adaptive reuse</div>}
      </div>
      {result && (
        <div className="future-image-meta">
          <span>{result.backend}</span><span>{result.width}×{result.height}</span><span>seed {result.seed}</span><span>{result.elapsed_s}s</span>
        </div>
      )}
      {result?.notice && <div className="pipeline-note"><span>i</span>{result.notice}</div>}
      {error && <div className="system-alert compact"><span>!</span> {error}</div>}
      {onGenerate && (
        <button className="btn ghost block image-action" disabled={disabled || busy} onClick={onGenerate}>
          <span>{result ? 'Regenerate image' : 'Generate reuse image'}</span><b aria-hidden="true">{busy ? '•••' : '↗'}</b>
        </button>
      )}
      <p className="truth-note image-truth">Concept visualization only. Physical, regulatory, market, and financial fit still require review.</p>
    </div>
  );
}
