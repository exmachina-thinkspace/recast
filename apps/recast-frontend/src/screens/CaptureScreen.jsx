import { useRef, useState } from 'react';
import { Logo, ProgressBar, Pill } from '../components.jsx';
import { analyzeImage, askAgent, transcribe } from '../api.js';

// Real pipeline calls, not a simulated scan -- per explicit direction:
// every button here hits an actual backend (Cosmos vision via /analyze-image,
// or the full tool-calling agent via /chat, which can call
// describe_camera_view / search_* tools for real).
export default function CaptureScreen({ onNext }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);
  const [recording, setRecording] = useState(false);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);

  async function runImageUpload(file) {
    setBusy(true); setError(null); setResult(null);
    try {
      const data = await analyzeImage(file);
      setResult({ label: 'Photo analysis (Cosmos vision AI)', text: data.description });
    } catch (e) { setError(e.message); }
    setBusy(false);
  }

  async function runQuickAsk(question, label) {
    setBusy(true); setError(null); setResult(null);
    try {
      const data = await askAgent(question);
      setResult({ label, text: data.answer, trace: data.tool_trace });
    } catch (e) { setError(e.message); }
    setBusy(false);
  }

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunksRef.current = [];
      const mr = new MediaRecorder(stream);
      mr.ondataavailable = (e) => chunksRef.current.push(e.data);
      mr.onstop = onRecordingStop;
      mr.start();
      mediaRecorderRef.current = mr;
      setRecording(true);
    } catch (e) { setError('mic error: ' + e.message); }
  }
  function stopRecording() {
    const mr = mediaRecorderRef.current;
    if (mr && mr.state !== 'inactive') { mr.stop(); mr.stream.getTracks().forEach(t => t.stop()); }
    setRecording(false);
  }
  async function onRecordingStop() {
    const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
    setBusy(true); setError(null);
    try {
      const t = await transcribe(blob);
      if (!t.text) { setError('heard nothing, try again'); setBusy(false); return; }
      const data = await askAgent(t.text);
      setResult({ label: `You asked: "${t.text}"`, text: data.answer, trace: data.tool_trace });
    } catch (e) { setError(e.message); }
    setBusy(false);
  }

  return (
    <div className="screen">
      <Logo />
      <ProgressBar step={2} />
      <Pill tone="blue">📱 Walk it with your iPhone</Pill>
      <h1 className="headline">Now let's see what the records can't tell you.</h1>
      <p className="subhead">Every button below calls a real backend pipeline — vision AI, live search tools, or the full agent. Nothing here is simulated.</p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => e.target.files[0] && runImageUpload(e.target.files[0])} />
        <button className="btn ghost block" disabled={busy} onClick={() => fileRef.current.click()}>📷 Analyze a photo (Cosmos vision)</button>
        <button className="btn ghost block" disabled={busy} onClick={() => runQuickAsk('What does the lobby camera view look like right now?', 'Lobby camera view')}>🏢 Describe the lobby (real camera frame)</button>
        <button
          className={`btn ${recording ? 'primary' : 'ghost'} block`}
          disabled={busy}
          onMouseDown={startRecording} onMouseUp={stopRecording}
          onTouchStart={startRecording} onTouchEnd={stopRecording}
        >
          🎙️ {recording ? 'Listening… release to send' : 'Hold to ask the agent anything'}
        </button>
      </div>

      {busy && <p className="subhead">Calling the real pipeline…</p>}
      {error && <p className="subhead" style={{ color: '#c0392b' }}>{error}</p>}
      {result && (
        <div className="card" style={{ alignItems: 'flex-start', textAlign: 'left' }}>
          <div className="score-label">{result.label}</div>
          <p className="subhead" style={{ color: 'var(--ink)' }}>{result.text}</p>
          {result.trace && result.trace.length > 0 && (
            <div className="tool-trace">real tools called: {result.trace.map(t => t.tool).join(', ')}</div>
          )}
        </div>
      )}

      <button className="btn primary block" onClick={onNext}>See the possibilities →</button>
    </div>
  );
}
