import { useEffect, useState } from 'react';
import { Logo, ProgressBar } from '../components.jsx';
import { getBuildingDetail } from '../api.js';

// Honest note on scope: this re-fetches the building's current real score
// (whatever the last generate_city_bhi.py/build_vitals_v2.py run
// produced), not a live recompute triggered by this specific walkthrough
// -- there's no backend endpoint yet that reruns scoring on demand from
// the browser. Shown as "before" vs "current on file", not overclaimed as
// "changed because of this session".
export default function UpdatedScoreScreen({ buildingId, beforeScore, onBackToMap }) {
  const [after, setAfter] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getBuildingDetail(buildingId).then((d) => setAfter(d.bhi_record?.bhi ?? null)).catch((e) => setError(e.message));
  }, [buildingId]);

  return (
    <div className="screen updated-screen">
      <header className="screen-header"><Logo /><div className="record-id">SYNTHESIS / CURRENT</div></header>
      <ProgressBar step={4} />
      <div className="eyebrow"><span>05</span> EVIDENCE SYNTHESIS</div>
      <h1 className="headline">One building.<br /><em>The full picture.</em></h1>
      <p className="subhead">The current score combines the records baseline with the latest evidence on file.</p>

      {error && <div className="system-alert"><span>!</span> {error}</div>}

      <div className="card synthesis-card">
        <div className="synthesis-rings" aria-hidden="true"><i /><i /><i /></div>
        <div className="before-after">
          <div className="before">
            <div className="val">{beforeScore != null ? Math.round(beforeScore) : '—'}</div>
            <div className="label">Records-only</div>
          </div>
          <div className="arrow">→</div>
          <div className="after">
            <div className="val">{after != null ? Math.round(after) : '…'}</div>
            <div className="label">On file now</div>
          </div>
        </div>
        <p className="subhead">
          {beforeScore != null && after != null && Math.round(beforeScore) === Math.round(after)
            ? 'No new scored evidence has been recorded for this building since the last run — the number is stable, not stale by omission.'
            : 'The difference reflects whatever evidence has been recorded since the records-only baseline.'}
        </p>
      </div>

      <button className="btn ghost block" onClick={onBackToMap}><span>Return to city view</span><b aria-hidden="true">↺</b></button>
    </div>
  );
}
