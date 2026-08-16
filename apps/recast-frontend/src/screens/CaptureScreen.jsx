import { useEffect, useRef, useState } from 'react';
import { Logo, ProgressBar, ReuseImagePanel } from '../components.jsx';
import {
  API,
  FRONTEND_ORIGIN,
  analyzeImage,
  askAgent,
  detectLensObjects,
  fileToDataUrl,
  generateReuseImage,
  getLensBridgeHealth,
  interpretLensFrame,
  sendLensFrame,
  transcribe,
} from '../api.js';

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
  const recordingStartedAtRef = useRef(0);
  const holdingToRecordRef = useRef(false);
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
      // Note: don't abort here if holdingToRecordRef went false while we were
      // awaiting the permission prompt -- the browser's native dialog can
      // fire a pointercancel on the held button while it has focus, which is
      // not the user releasing early. The elapsed/size check below already
      // catches genuinely too-short recordings.
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

      {cameraBlockedByOrigin && (
        <div className="lens-warning">
          <strong>Camera access is blocked on this URL.</strong>
          <span>iPhone Safari requires HTTPS for live camera access. Open Recast over HTTPS on the phone, then try again.</span>
        </div>
      )}

      <div className="lens-panel">
        <div className="score-label">RECAST LENS / GN100 LIVE</div>
        <div className="lens-video-wrap">
          <video ref={videoRef} className="lens-video" playsInline muted />
          {!cameraReady && (
            <div className="lens-placeholder">
              <div className="score-label">Camera idle</div>
              <p className="subhead">Start the iPhone camera, then stream lightweight frames to the GN100 bridge.</p>
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
            <span>Frames</span>
            <strong>{framesSent}</strong>
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
        <div className="lens-controls">
          <button className="btn ghost block" disabled={!lastFrameAt || interpreting} onClick={runLensInterpretation}>
            {interpreting ? 'Asking NVIDIA vision...' : 'What am I seeing?'}
          </button>
          <button className="btn ghost block" disabled={!lastFrameAt || detectingObjects} onClick={runObjectDetection}>
            {detectingObjects ? 'Detecting objects...' : 'Identify objects'}
          </button>
        </div>
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
          <span className="action-code">VOC</span><span>{recording ? 'Listening - release to send' : 'Hold to ask Recast'}</span><b>{recording ? '●' : '↗'}</b>
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
