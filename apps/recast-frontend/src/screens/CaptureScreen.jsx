import { useEffect, useRef, useState } from 'react';
import { Logo, ProgressBar, Pill } from '../components.jsx';
import { API, FRONTEND_ORIGIN, analyzeImage, askAgent, detectLensObjects, getLensBridgeHealth, interpretLensFrame, sendLensFrame, transcribe } from '../api.js';

const FRAME_INTERVAL_MS = 200;
const FRAME_TARGET_WIDTH = 640;
const FRAME_JPEG_QUALITY = 0.55;
const OBJECT_DETECT_INTERVAL_MS = 5000;

function formatObjectFreshness(objects) {
  if (!objects?.created_at_iso) return 'none';
  const ageSeconds = Math.max(0, Math.round((Date.now() - new Date(objects.created_at_iso).getTime()) / 1000));
  return `${ageSeconds}s ago`;
}

function ObjectBoxOverlay({ objects }) {
  const detections = objects?.objects || [];
  const width = objects?.image_size?.width;
  const height = objects?.image_size?.height;
  if (!detections.length || !width || !height) return null;

  return (
    <div className="lens-object-overlay" aria-label={`${detections.length} detected objects`}>
      {detections.slice(0, 20).map((obj, index) => {
        const [x1, y1, x2, y2] = obj.bbox_xyxy || [];
        if ([x1, y1, x2, y2].some((value) => typeof value !== 'number')) return null;
        const left = Math.max(0, Math.min(100, (x1 / width) * 100));
        const top = Math.max(0, Math.min(100, (y1 / height) * 100));
        const boxWidth = Math.max(1, Math.min(100 - left, ((x2 - x1) / width) * 100));
        const boxHeight = Math.max(1, Math.min(100 - top, ((y2 - y1) / height) * 100));
        return (
          <div
            className="lens-object-box"
            key={`${obj.label}-${index}-${x1}-${y1}`}
            style={{ left: `${left}%`, top: `${top}%`, width: `${boxWidth}%`, height: `${boxHeight}%` }}
          >
            <span>{obj.label} {Math.round((obj.confidence || 0) * 100)}%</span>
          </div>
        );
      })}
    </div>
  );
}

export default function CaptureScreen({ onNext }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const frameTimerRef = useRef(null);
  const objectTimerRef = useRef(null);
  const objectDetectionInFlightRef = useRef(false);
  const liveObjectTrackingRef = useRef(true);
  const cameraStreamRef = useRef(null);
  const [cameraReady, setCameraReady] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [bridgeStatus, setBridgeStatus] = useState('unchecked');
  const [framesSent, setFramesSent] = useState(0);
  const [lastFrameAt, setLastFrameAt] = useState(null);
  const [interpretation, setInterpretation] = useState(null);
  const [interpreting, setInterpreting] = useState(false);
  const [objects, setObjects] = useState(null);
  const [detectingObjects, setDetectingObjects] = useState(false);
  const [liveObjectTracking, setLiveObjectTracking] = useState(true);
  const [sessionId] = useState(() => `recast-lens-${Date.now()}`);
  const [recording, setRecording] = useState(false);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const cameraSupported = Boolean(navigator.mediaDevices?.getUserMedia);
  const secureCameraOrigin = window.isSecureContext;
  const cameraBlockedByOrigin = !secureCameraOrigin || !cameraSupported;

  useEffect(() => {
    checkBridge();
    return () => {
      stopObjectTracking();
      stopFrameStream();
      stopCamera();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function checkBridge() {
    try {
      const data = await getLensBridgeHealth();
      setBridgeStatus(data.ok ? 'online' : 'unhealthy');
    } catch {
      setBridgeStatus('offline');
    }
  }

  async function startCamera() {
    setError(null);
    if (!secureCameraOrigin) {
      setError(`camera blocked: ${FRONTEND_ORIGIN} is not a secure browser origin. Open Recast over HTTPS on the phone, then try again.`);
      return;
    }
    if (!cameraSupported) {
      setError('camera blocked: this browser does not expose getUserMedia for this page. On iPhone, use Safari from an HTTPS origin.');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: 'environment' },
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      });
      cameraStreamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setCameraReady(true);
    } catch (e) {
      setError(`camera error: ${e.message}`);
      setCameraReady(false);
    }
  }

  function stopCamera() {
    stopFrameStream();
    cameraStreamRef.current?.getTracks().forEach((track) => track.stop());
    cameraStreamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setCameraReady(false);
  }

  function startFrameStream() {
    if (!cameraReady) {
      setError('start the camera before streaming frames');
      return;
    }
    setError(null);
    setFramesSent(0);
    setObjects(null);
    setStreaming(true);
    frameTimerRef.current = window.setInterval(captureAndSendFrame, FRAME_INTERVAL_MS);
    captureAndSendFrame();
  }

  function stopFrameStream() {
    if (frameTimerRef.current) window.clearInterval(frameTimerRef.current);
    frameTimerRef.current = null;
    stopObjectTracking();
    setStreaming(false);
  }

  function startObjectTracking() {
    if (objectTimerRef.current) return;
    runObjectDetection({ clear: false, quiet: true });
    objectTimerRef.current = window.setInterval(() => {
      runObjectDetection({ clear: false, quiet: true });
    }, OBJECT_DETECT_INTERVAL_MS);
  }

  function stopObjectTracking() {
    if (objectTimerRef.current) window.clearInterval(objectTimerRef.current);
    objectTimerRef.current = null;
  }

  function toggleObjectTracking() {
    const next = !liveObjectTracking;
    liveObjectTrackingRef.current = next;
    setLiveObjectTracking(next);
    if (next && streaming && lastFrameAt) {
      startObjectTracking();
    } else {
      stopObjectTracking();
    }
  }

  async function captureAndSendFrame() {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !video.videoWidth || !video.videoHeight) return;

    const targetWidth = FRAME_TARGET_WIDTH;
    const targetHeight = Math.round((video.videoHeight / video.videoWidth) * targetWidth);
    canvas.width = targetWidth;
    canvas.height = targetHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, targetWidth, targetHeight);

    canvas.toBlob(async (blob) => {
      if (!blob) return;
      try {
        await sendLensFrame(blob, { sessionId, deviceLabel: 'iphone-browser-camera' });
        setFramesSent((count) => count + 1);
        setLastFrameAt(new Date());
        setBridgeStatus('online');
        if (liveObjectTrackingRef.current) startObjectTracking();
      } catch (e) {
        setError(e.message);
        setBridgeStatus('offline');
        stopFrameStream();
      }
    }, 'image/jpeg', FRAME_JPEG_QUALITY);
  }

  async function runLensInterpretation() {
    setInterpreting(true);
    setError(null);
    setInterpretation(null);
    try {
      const data = await interpretLensFrame();
      setInterpretation(data);
      setBridgeStatus('online');
    } catch (e) {
      setError(e.message);
    } finally {
      setInterpreting(false);
    }
  }

  async function runObjectDetection({ clear = true, quiet = false } = {}) {
    if (objectDetectionInFlightRef.current) return;
    objectDetectionInFlightRef.current = true;
    setDetectingObjects(true);
    if (!quiet) setError(null);
    if (clear) setObjects(null);
    try {
      const data = await detectLensObjects();
      setObjects(data);
      setBridgeStatus('online');
    } catch (e) {
      if (!quiet) setError(e.message);
    } finally {
      objectDetectionInFlightRef.current = false;
      setDetectingObjects(false);
    }
  }

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
      <Pill tone={streaming ? 'green' : 'blue'}>{streaming ? 'Live frame stream' : 'Recast Lens v1'}</Pill>
      <h1 className="headline">Stream the iPhone camera to the GN100.</h1>
      <p className="subhead">This first version uses our own browser camera code and Recast Lens bridge on port 8910. It does not use Larix or the occupied 8099 prototype.</p>
      {cameraBlockedByOrigin && (
        <div className="lens-warning">
          <strong>Camera access is blocked on this URL.</strong>
          <span>iPhone Safari requires HTTPS for live camera access. The page can load over HTTP, but `Start camera` will not open the camera until this frontend is served from a secure origin.</span>
        </div>
      )}

      <div className="lens-panel">
        <div className="lens-video-wrap">
          <video ref={videoRef} className="lens-video" playsInline muted />
          {!cameraReady && (
            <div className="lens-placeholder">
              <div className="score-label">Camera idle</div>
              <p className="subhead">Start camera on the iPhone, then stream frames to the GN100 bridge.</p>
            </div>
          )}
          <ObjectBoxOverlay objects={objects} />
          {streaming && <div className="lens-live">LIVE</div>}
        </div>
        <canvas ref={canvasRef} hidden />

        <div className="lens-status-grid">
          <div>
            <span>Bridge</span>
            <strong className={bridgeStatus === 'online' ? 'ok' : bridgeStatus === 'offline' ? 'bad' : ''}>{bridgeStatus}</strong>
          </div>
          <div>
            <span>Target</span>
            <strong>{API.lensBridge ? API.lensBridge.replace(/^https?:\/\//, '') : 'same-origin proxy'}</strong>
          </div>
          <div>
            <span>Origin</span>
            <strong className={secureCameraOrigin ? 'ok' : 'bad'}>{secureCameraOrigin ? 'secure' : 'http blocked'}</strong>
          </div>
          <div>
            <span>Frames</span>
            <strong>{framesSent}</strong>
          </div>
          <div>
            <span>Last frame</span>
            <strong>{lastFrameAt ? lastFrameAt.toLocaleTimeString() : 'none'}</strong>
          </div>
          <div>
            <span>Objects</span>
            <strong className={liveObjectTracking ? 'ok' : ''}>{liveObjectTracking ? (detectingObjects ? 'tracking...' : 'live tracking') : 'manual'}</strong>
          </div>
          <div>
            <span>AI updated</span>
            <strong>{formatObjectFreshness(objects)}</strong>
          </div>
        </div>

        <div className="lens-controls">
          {!cameraReady ? (
            <button className="btn primary block" disabled={busy || cameraBlockedByOrigin} onClick={startCamera}>Start camera</button>
          ) : (
            <button className="btn ghost block" disabled={streaming} onClick={stopCamera}>Stop camera</button>
          )}
          {!streaming ? (
            <button className="btn primary block" disabled={!cameraReady} onClick={startFrameStream}>Stream to GN100</button>
          ) : (
            <button className="btn ghost block" onClick={stopFrameStream}>Stop stream</button>
          )}
        </div>
        <button className="btn ghost block" disabled={!lastFrameAt || interpreting} onClick={runLensInterpretation}>
          {interpreting ? 'Asking NVIDIA vision...' : 'What am I seeing?'}
        </button>
        <button className="btn ghost block" disabled={!lastFrameAt || detectingObjects} onClick={runObjectDetection}>
          {detectingObjects ? 'Detecting objects...' : 'Identify objects'}
        </button>
        <button className={`btn ${liveObjectTracking ? 'primary' : 'ghost'} block`} disabled={!streaming && !lastFrameAt} onClick={toggleObjectTracking}>
          {liveObjectTracking ? 'Live object tracking on' : 'Live object tracking off'}
        </button>
        {objects && (
          <div className="lens-answer">
            <div className="score-label">Local object detector</div>
            <p>{objects.count} objects detected{Object.keys(objects.summary || {}).length ? `: ${Object.entries(objects.summary).map(([label, count]) => `${label} ${count}`).join(', ')}` : '.'}</p>
            {objects.objects?.length > 0 && (
              <span>{objects.objects.slice(0, 6).map((obj) => `${obj.label} ${(obj.confidence * 100).toFixed(0)}%`).join(' · ')} · AI {formatObjectFreshness(objects)}</span>
            )}
          </div>
        )}
        {interpretation && (
          <div className="lens-answer">
            <div className="score-label">Local NVIDIA vision</div>
            <p>{interpretation.description}</p>
            <span>{interpretation.engine} · {interpretation.elapsed_s}s</span>
          </div>
        )}
      </div>

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
