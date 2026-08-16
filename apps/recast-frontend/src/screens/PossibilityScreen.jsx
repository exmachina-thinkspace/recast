import { useEffect, useState } from 'react';
import { Logo, ProgressBar, Pill, ReuseImagePanel } from '../components.jsx';
import { askAgent, generateReuseImage } from '../api.js';

// Real call to whats_next_for_building (recast_view.py's room-size
// heuristic + a Cosmos-grounded description), via the same agent used
// everywhere else in this app -- not a separate mocked step.
export default function PossibilityScreen({ building, roomContext, onRoomContext, onNext }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [reuseTarget, setReuseTarget] = useState(roomContext?.proposedUse || '');
  const [generatedImage, setGeneratedImage] = useState(roomContext?.generatedImage || null);
  const [imageBusy, setImageBusy] = useState(false);
  const [imageError, setImageError] = useState(null);
  const [seed, setSeed] = useState(roomContext?.generatedImage?.seed ?? 7);

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

  useEffect(() => {
    if (top?.[0] && !reuseTarget) setReuseTarget(top[0]);
  }, [top, reuseTarget]);

  async function renderFuture(regenerate = false) {
    if (!reuseTarget.trim()) return;
    const nextSeed = regenerate ? seed + 1 : seed;
    setSeed(nextSeed);
    setImageBusy(true); setImageError(null);
    try {
      const generated = await generateReuseImage({
        building: building?.n || building?.a || 'the selected building',
        currentUse: (roomContext?.description || 'an underused interior space').slice(0, 420),
        proposedUse: reuseTarget,
        image: roomContext?.file,
        seed: nextSeed,
        extras: 'Show a credible adaptive-reuse concept while retaining existing structural geometry. No claim of final feasibility.',
      });
      setGeneratedImage(generated);
      if (onRoomContext) {
        onRoomContext({ ...(roomContext || {}), proposedUse: reuseTarget.trim(), generatedImage: generated });
      }
    } catch (e) {
      setImageError(e.message);
    }
    setImageBusy(false);
  }

  return (
    <div className="screen possibility-screen">
      <header className="screen-header"><Logo /><div className="record-id">MODEL / T3</div></header>
      <ProgressBar step={3} />
      <div className="eyebrow"><span>04</span> POSSIBILITY ENGINE</div>
      <h1 className="headline">Model what this<br /><em>could become.</em></h1>
      <p className="subhead">Reuse potential derived from measured room geometry—not a visual guess.</p>

      {error && <div className="system-alert"><span>!</span> {error}</div>}
      {!data && !error && <div className="pipeline-status"><span className="scanner" /> Generating reuse model</div>}

      {data && top && (
        <div className="card possibility-card">
          <div className="reuse-model" aria-label={`${top[1]} rooms identified for ${top[0]}`}>
            <div className="reuse-tower" aria-hidden="true">
              {Array.from({ length: Math.min(top[1], 7) }, (_, i) => <i key={i} />)}
            </div>
            <div className="reuse-axis" aria-hidden="true"><span>Z</span><span>Y</span><span>X</span></div>
            <span className="capture-label capture-label--top">MASSING / REUSE FIT</span>
          </div>
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
          <p className="subhead possibility-answer">{data.answer}</p>
          <div className="tool-trace">T3 · deterministic room-size heuristic · recast_view.py</div>
        </div>
      )}

      {data && top && (
        <div className="card future-proposal-card">
          <div className="score-label">ALTERNATIVE FUTURE / VISUAL</div>
          <p className="subhead">
            {roomContext?.file
              ? 'The uploaded room will guide the layout so the render stays tied to this space.'
              : 'No room photo is attached, so this will be a concept image rather than a structure-preserving render.'}
          </p>
          <label className="field-label" htmlFor="possibility-use">Future use to visualize</label>
          <input
            id="possibility-use"
            className="reuse-request-input"
            value={reuseTarget}
            onChange={(e) => setReuseTarget(e.target.value)}
          />
          {entries.length > 1 && (
            <div className="reuse-choice-row">
              {entries.slice(0, 3).map(([use]) => (
                <button key={use} className={reuseTarget === use ? 'active' : ''} onClick={() => setReuseTarget(use)}>{use}</button>
              ))}
            </div>
          )}
          <ReuseImagePanel
            result={generatedImage}
            sourcePreview={roomContext?.previewUrl}
            busy={imageBusy}
            error={imageError}
            proposedUse={reuseTarget}
            disabled={!reuseTarget.trim()}
            onGenerate={() => renderFuture(Boolean(generatedImage))}
          />
        </div>
      )}

      <button className="btn primary block" onClick={onNext} disabled={!data}><span>Synthesize the evidence</span><b aria-hidden="true">→</b></button>
    </div>
  );
}
