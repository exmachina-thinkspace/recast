import { useEffect, useState } from 'react';
import { Logo, ProgressBar, Pill, scoreColor } from '../components.jsx';
import { getBuildingDetail } from '../api.js';

const SOURCE_ICONS = {
  use_utilization: '📍 Occupancy / leasing',
  clean_safety: '🛡️ Safety & compliance',
  economic: '💰 Economic records',
  community: '🏙️ Community activity',
  productivity_upkeep: '🛠️ Upkeep / permits',
};

export default function ScoreScreen({ buildingId, onNext, onScoreLoaded }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setDetail(null);
    getBuildingDetail(buildingId).then((d) => {
      setDetail(d);
      if (onScoreLoaded) onScoreLoaded(d.bhi_record?.bhi ?? null);
    }).catch((e) => setError(e.message));
  }, [buildingId]);

  if (error) return <div className="screen"><Logo /><p className="subhead">Error: {error}</p></div>;
  if (!detail) return <div className="screen"><Logo /><p className="subhead">Loading real score…</p></div>;

  const { building, bhi_record } = detail;
  if (!bhi_record) {
    return (
      <div className="screen">
        <Logo />
        <ProgressBar step={1} />
        <Pill tone="neutral">Starting score</Pill>
        <h1 className="headline">Insufficient evidence for this building yet.</h1>
        <p className="subhead">{building.a} hasn't been scored. Try another building from the map.</p>
      </div>
    );
  }

  const vitals = Object.entries(bhi_record.vitals);

  return (
    <div className="screen">
      <Logo />
      <ProgressBar step={1} />
      <Pill tone="blue">📋 Starting score</Pill>
      <h1 className="headline">This building scores {Math.round(bhi_record.bhi)} out of 100 right now.</h1>
      <p className="subhead">That score comes from public records and city data — real numbers, every gap disclosed honestly.</p>

      <div className="card">
        <Pill tone="blue">📍 {building.a}</Pill>
        <div className="score-highlight">
          <div className="bg" />
          <span style={{ color: scoreColor(bhi_record.bhi) }}>{Math.round(bhi_record.bhi)}</span>
        </div>
        <div className="score-label">Recast score — {Math.round(bhi_record.evidence_coverage * 100)}% evidence coverage</div>
        <div className="evidence-tags">
          {vitals.map(([key, v]) => (
            <Pill key={key} tone={v.evidence_coverage >= 0.6 ? 'green' : 'neutral'}>
              {SOURCE_ICONS[key] || key} {v.evidence_coverage >= 0.6 ? '✓' : ''}
            </Pill>
          ))}
        </div>
      </div>

      <button className="btn primary block" onClick={onNext}>Walk the space →</button>
    </div>
  );
}
