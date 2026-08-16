import { useEffect, useState } from 'react';
import { Logo, ProgressBar } from '../components.jsx';
import { scoreColor } from '../scoreUtils.js';
import { getBuildingDetail } from '../api.js';

const SOURCE_ICONS = {
  use_utilization: 'Occupancy / leasing',
  clean_safety: 'Safety & compliance',
  economic: 'Economic records',
  community: 'Community activity',
  productivity_upkeep: 'Upkeep / permits',
};

export default function ScoreScreen({ buildingId, onNext, onScoreLoaded, onBuildingLoaded }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setDetail(null);
    getBuildingDetail(buildingId).then((d) => {
      setDetail(d);
      if (onScoreLoaded) onScoreLoaded(d.bhi_record?.bhi ?? null);
      if (onBuildingLoaded) onBuildingLoaded(d.building);
    }).catch((e) => setError(e.message));
  }, [buildingId, onScoreLoaded, onBuildingLoaded]);

  if (error) return <div className="screen"><Logo /><div className="system-alert"><span>!</span> {error}</div></div>;
  if (!detail) return <div className="screen loading-screen"><Logo /><span className="scanner" /><p className="subhead">Resolving the building record…</p></div>;

  const { building, bhi_record } = detail;
  if (!bhi_record) {
    return (
      <div className="screen">
        <header className="screen-header"><Logo /><div className="record-id">NO RECORD</div></header>
        <ProgressBar step={1} />
        <div className="eyebrow"><span>02</span> EVIDENCE BASELINE</div>
        <h1 className="headline">Insufficient evidence for this building yet.</h1>
        <p className="subhead">{building.a} hasn't been scored. Try another building from the map.</p>
      </div>
    );
  }

  const vitals = Object.entries(bhi_record.vitals);

  return (
    <div className="screen score-screen">
      <header className="screen-header"><Logo /><div className="record-id">ASSET / {building.i ?? buildingId}</div></header>
      <ProgressBar step={1} />
      <div className="eyebrow"><span>02</span> EVIDENCE BASELINE</div>
      <h1 className="headline">A score you can<br /><em>see through.</em></h1>
      <p className="subhead">Public records and city data establish the baseline. Every signal and every gap stays visible.</p>

      <div className="card score-card">
        <div className="score-address"><span /> {building.a}</div>
        <div className="score-radar" aria-label={`Building Health Index ${Math.round(bhi_record.bhi)} out of 100`}>
          <i className="score-orbit score-orbit--outer" aria-hidden="true" />
          <i className="score-orbit score-orbit--inner" aria-hidden="true" />
          <div className="score-highlight">
            <div className="bg" />
            <span style={{ color: scoreColor(bhi_record.bhi) }}>{Math.round(bhi_record.bhi)}</span>
          </div>
          <div className="score-unit">/ 100</div>
        </div>
        <div className="score-label">BUILDING HEALTH INDEX · {Math.round(bhi_record.evidence_coverage * 100)}% EVIDENCE COVERAGE</div>
        <div className="vitals-stack">
          {vitals.map(([key, v], index) => {
            const coverage = Math.round(v.evidence_coverage * 100);
            return (
              <div className="vital-row" key={key}>
                <span className="vital-index">0{index + 1}</span>
                <div className="vital-copy">
                  <div><strong>{SOURCE_ICONS[key] || key}</strong><span>{coverage >= 60 ? 'EVIDENCE' : 'OPEN'}</span></div>
                  <i><b style={{ width: `${coverage}%` }} /></i>
                </div>
                <span className="vital-value">{coverage}%</span>
              </div>
            );
          })}
        </div>
      </div>

      <button className="btn primary block" onClick={onNext}><span>Walk the space</span><b aria-hidden="true">→</b></button>
    </div>
  );
}
