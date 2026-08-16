import { useRef, useState } from 'react';
import { Logo, ProgressBar, ReuseImagePanel } from '../components.jsx';
import { analyzeImage, askAgent, fileToDataUrl, generateReuseImage, transcribe } from '../api.js';

// Real pipeline calls, not a simulated scan -- per explicit direction:
// every button here hits an actual backend (Cosmos vision via /analyze-image,
// or the full tool-calling agent via /chat, which can call
// describe_camera_view / search_* tools for real).
export default function CaptureScreen({ building, roomContext, onRoomContext, onNext }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [room, setRoom] = useState(roomContext);
  const [reuseRequest, setReuseRequest] = useState(roomContext?.proposedUse || '');
  const [generatedImage, setGeneratedImage] = useState(roomContext?.generatedImage || null);
  const [imageBusy, setImageBusy] = useState(false);
  const [imageError, setImageError] = useState(null);
  const [seed, setSeed] = useState(roomContext?.generatedImage?.seed ?? 7);
  const fileRef = useRef(null);
  const [recording, setRecording] = useState(false);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const recordingStartedAtRef = useRef(0);
  const holdingToRecordRef = useRef(false);

  async function runImageUpload(file) {
    setBusy(true); setError(null); setResult(null);
    try {
      const previewUrl = await fileToDataUrl(file);
      const baseRoom = { file, previewUrl, description: null, proposedUse: '', generatedImage: null };
      setRoom(baseRoom);
      setGeneratedImage(null);
      setImageError(null);
      if (onRoomContext) onRoomContext(baseRoom);
      const data = await analyzeImage(file);
      const nextRoom = { ...baseRoom, description: data.description, analysisSource: data.source };
      setRoom(nextRoom);
      if (onRoomContext) onRoomContext(nextRoom);
      setResult({ label: 'Photo analysis (Cosmos vision AI)', text: data.description });
    } catch (e) { setError(e.message); }
    setBusy(false);
  }

  async function renderReuse(regenerate = false) {
    if (!room?.file || !reuseRequest.trim()) return;
    const nextSeed = regenerate ? seed + 1 : seed;
    setSeed(nextSeed);
    setImageBusy(true); setImageError(null);
    try {
      const generated = await generateReuseImage({
        building: building?.n || building?.a || 'the selected building',
        currentUse: (room.description || 'the uploaded room').slice(0, 420),
        proposedUse: reuseRequest,
        image: room.file,
        seed: nextSeed,
        extras: 'Preserve the visible room geometry. Treat this as an adaptive-reuse concept, not a feasibility claim.',
      });
      const nextRoom = { ...room, proposedUse: reuseRequest.trim(), generatedImage: generated };
      setGeneratedImage(generated);
      setRoom(nextRoom);
      if (onRoomContext) onRoomContext(nextRoom);
    } catch (e) {
      setImageError(e.message);
    }
    setImageBusy(false);
  }

  async function runQuickAsk(question, label) {
    setBusy(true); setError(null); setResult(null);
    try {
      const data = await askAgent(question);
      setResult({ label, text: data.answer, trace: data.tool_trace });
    } catch (e) { setError(e.message); }
    setBusy(false);
  }

  async function startRecording(event) {
    if (recording || mediaRecorderRef.current?.state === 'recording') return;
    event?.preventDefault();
    holdingToRecordRef.current = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!holdingToRecordRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        setError('Hold the microphone button while permission is granted, then speak for at least one second.');
        return;
      }
      chunksRef.current = [];
      const supportedMime = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/mp4',
      ].find((type) => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(stream, supportedMime ? { mimeType: supportedMime } : undefined);
      recorder.ondataavailable = (dataEvent) => {
        if (dataEvent.data?.size) chunksRef.current.push(dataEvent.data);
      };
      recorder.onerror = () => setError('Microphone recording failed. Check the browser microphone permission and try again.');
      recorder.onstop = () => onRecordingStop(
        recorder.mimeType || supportedMime || 'audio/webm',
        performance.now() - recordingStartedAtRef.current,
      );
      recorder.start(250);
      mediaRecorderRef.current = recorder;
      recordingStartedAtRef.current = performance.now();
      setRecording(true);
    } catch (e) {
      holdingToRecordRef.current = false;
      setError('mic error: ' + e.message);
    }
  }
  function stopRecording(event) {
    event?.preventDefault();
    holdingToRecordRef.current = false;
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== 'inactive') {
      recorder.requestData();
      recorder.stop();
      recorder.stream.getTracks().forEach((track) => track.stop());
    }
    setRecording(false);
  }
  async function onRecordingStop(mimeType, elapsed) {
    const blob = new Blob(chunksRef.current, { type: mimeType });
    mediaRecorderRef.current = null;
    if (elapsed < 700 || blob.size < 1024) {
      setError('Recording was too short. Hold the microphone button for at least one second.');
      return;
    }
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
    <div className="screen capture-screen">
      <header className="screen-header"><Logo /><div className="record-id">FIELD / LIVE</div></header>
      <ProgressBar step={2} />
      <div className="eyebrow"><span>03</span> FIELD CAPTURE</div>
      <h1 className="headline">Turn the physical<br /><em>into signal.</em></h1>
      <p className="subhead">See what the records cannot. Every capture runs through the real vision or agent pipeline.</p>

      <div className={`capture-viewport ${room?.previewUrl ? 'has-room-photo' : ''}`}>
        {room?.previewUrl ? (
          <img className="room-source-image" src={room.previewUrl} alt="Uploaded room" />
        ) : (
          <>
            <div className="capture-grid" aria-hidden="true" />
            <div className="scan-volume" aria-hidden="true"><i /><i /><i /></div>
          </>
        )}
        <div className="scan-plane" aria-hidden="true" />
        <span className="capture-label capture-label--top">{room?.previewUrl ? 'ROOM SOURCE / CAPTURED' : 'SPATIAL SCAN / READY'}</span>
        <span className="capture-label capture-label--bottom">COSMOS VISION LINK</span>
      </div>

      <div className="action-stack">
        <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => e.target.files[0] && runImageUpload(e.target.files[0])} />
        <button className="btn ghost block action-button" disabled={busy} onClick={() => fileRef.current.click()}><span className="action-code">IMG</span><span>Analyze a photo</span><b>↗</b></button>
        <button className="btn ghost block action-button" disabled={busy} onClick={() => runQuickAsk('What does the lobby camera view look like right now?', 'Lobby camera view')}><span className="action-code">CAM</span><span>Read the lobby camera</span><b>↗</b></button>
        <button
          className={`btn ${recording ? 'primary' : 'ghost'} block action-button voice-action`}
          disabled={busy}
          onPointerDown={startRecording} onPointerUp={stopRecording}
          onPointerCancel={stopRecording}
        >
          <span className="action-code">VOC</span><span>{recording ? 'Listening — release to send' : 'Hold to ask Recast'}</span><b>{recording ? '●' : '↗'}</b>
        </button>
      </div>

      {busy && <div className="pipeline-status"><span className="scanner" /> Calling the live pipeline</div>}
      {error && <div className="system-alert"><span>!</span> {error}</div>}
      {result && (
        <div className="card" style={{ alignItems: 'flex-start', textAlign: 'left' }}>
          <div className="score-label">{result.label}</div>
          <p className="subhead" style={{ color: 'var(--ink)' }}>{result.text}</p>
          {result.trace && result.trace.length > 0 && (
            <div className="tool-trace">real tools called: {result.trace.map(t => t.tool).join(', ')}</div>
          )}
        </div>
      )}

      {room?.previewUrl && (
        <div className="card room-reuse-card">
          <div className="score-label">REPURPOSE THIS SPACE</div>
          <p className="subhead">Describe a future use. The uploaded room becomes the structural reference for a depth-guided render.</p>
          <label className="field-label" htmlFor="reuse-request">Proposed use</label>
          <textarea
            id="reuse-request"
            className="reuse-request-input"
            rows={3}
            value={reuseRequest}
            placeholder="Example: a community health clinic with flexible consultation rooms"
            onChange={(e) => setReuseRequest(e.target.value)}
          />
          <ReuseImagePanel
            result={generatedImage}
            sourcePreview={room.previewUrl}
            busy={imageBusy}
            error={imageError}
            proposedUse={reuseRequest}
            disabled={!reuseRequest.trim()}
            onGenerate={() => renderReuse(Boolean(generatedImage))}
          />
        </div>
      )}

      <button className="btn primary block" onClick={onNext}><span>See the possibilities</span><b aria-hidden="true">→</b></button>
    </div>
  );
}
