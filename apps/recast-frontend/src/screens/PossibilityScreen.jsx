import { useEffect, useState } from 'react';
import { Logo, ProgressBar, Pill } from '../components.jsx';
import { askAgent } from '../api.js';

// Real call to whats_next_for_building (recast_view.py's room-size
// heuristic + a Cosmos-grounded description), via the same agent used
// everywhere else in this app -- not a separate mocked step.
export default function PossibilityScreen({ onNext }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    askAgent('What could this building become? List the room reuse breakdown.')
      .then((res) => {
        const tool = (res.tool_trace || []).find(t => t.tool === 'whats_next_for_building');
        setData({ answer: res.answer, tool: tool ? tool.result : null });
      })
      .catch((e) => setError(e.message));
  }, []);

  const counts = data?.tool?.room_counts_by_use || {};
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const top = entries[0];
  const rest = entries.slice(1);

  return (
    <div className="screen">
      <Logo />
      <ProgressBar step={3} />
      <Pill tone="green">✨ What it could become</Pill>
      <h1 className="headline">This space is a great fit for more than you'd think.</h1>
      <p className="subhead">Based on real measured room geometry from the architectural plan — not a guess.</p>

      {error && <p className="subhead" style={{ color: '#c0392b' }}>{error}</p>}
      {!data && !error && <p className="subhead">Asking the agent…</p>}

      {data && top && (
        <div className="card" style={{ alignItems: 'stretch' }}>
          <div className="matches-row">
            <div className="match-card">
              <div className="match-badge">{top[1]}</div>
              <div className="meta">
                <div className="tag">Top count</div>
                <div className="name">{top[0]}</div>
              </div>
            </div>
          </div>
          {rest.length > 0 && (
            <>
              <div className="score-label" style={{ marginTop: 8 }}>Also present</div>
              <div className="evidence-tags">
                {rest.map(([use, n]) => <Pill key={use} tone="neutral">{use} — {n} rooms</Pill>)}
              </div>
            </>
          )}
          <p className="subhead" style={{ marginTop: 10, color: 'var(--ink)' }}>{data.answer}</p>
          <div className="tool-trace">T3 — deterministic room-size heuristic (recast_view.py), not a code review</div>
        </div>
      )}

      <button className="btn primary block" onClick={onNext} disabled={!data}>Update the score →</button>
    </div>
  );
}
