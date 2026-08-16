import { useEffect, useRef, useState } from 'react';
import { Logo, ReuseImagePanel } from '../components.jsx';
import { analyzeImage, askAgent, fileToDataUrl, generateReuseImage, getAgentHealth, transcribe } from '../api.js';

const VISUALIZE_REUSE_PROMPT = 'Generate an image of this room repurposed as a community health clinic';
const CHAT_STORAGE_KEY = 'recast-chat-history-v1';
const MAX_AGENT_HISTORY_TURNS = 12;

function wantsReuseImage(message, hasRoomPhoto) {
  const text = message.toLowerCase();
  const visualIntent = /\b(image|render|visuali[sz]e|picture|concept art)\b/.test(text);
  const generationIntent = /\b(generate|create|show me|regenerate)\b/.test(text);
  const reuseIntent = /\b(reuse|repurpose|convert|reimagine|re-imagine|turn|become|future use)\b/.test(text);
  return visualIntent || (generationIntent && reuseIntent) || (hasRoomPhoto && reuseIntent);
}

function proposedUseFrom(message, previousUse) {
  if (/\bregenerate\b/i.test(message) && previousUse) return previousUse;
  return message
    .replace(/^(please\s+)?(can you\s+|could you\s+)?/i, '')
    .replace(/^(generate|create|render|visuali[sz]e|show me)\s+(an?\s+)?(image|picture|render)?\s*(of\s+)?/i, '')
    .trim()
    .slice(0, 300) || previousUse || 'a flexible community space';
}

export default function ChatScreen({ building, roomContext, onRoomContext }) {
  const [log, setLog] = useState(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(CHAT_STORAGE_KEY) || '[]');
      if (!Array.isArray(saved)) return [];
      return saved.filter((item) => item?.role && item?.text).slice(0, 40);
    } catch {
      return [];
    }
  });
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [agentHealth, setAgentHealth] = useState(null);
  const [attachment, setAttachment] = useState(null);
  const [attachmentError, setAttachmentError] = useState(null);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const recordingStartedAtRef = useRef(0);
  const holdingToRecordRef = useRef(false);
  const fileInputRef = useRef(null);
  const messageIdRef = useRef(log.reduce((max, item) => Math.max(max, Number(item.id) || 0), 0));

  useEffect(() => {
    let active = true;
    async function refreshHealth() {
      try {
        const health = await getAgentHealth();
        if (active) setAgentHealth(health);
      } catch {
        if (active) setAgentHealth({ ok: false, agent_ready: false, transcription_ready: false, vision_ready: false });
      }
    }
    refreshHealth();
    const timer = window.setInterval(refreshHealth, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    const serializable = log
      .filter((item) => item.role && item.text)
      .slice(0, 40)
      .map(({ id, role, text, trace, analysisError }) => ({
        id,
        role,
        text,
        trace,
        analysisError,
      }));
    window.localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(serializable));
  }, [log]);

  function nextId() {
    messageIdRef.current += 1;
    return messageIdRef.current;
  }

  function updateMessage(id, patch) {
    setLog((items) => items.map((item) => item.id === id ? { ...item, ...patch } : item));
  }

  function buildAgentHistory(pendingUserMessage) {
    const turns = log
      .slice()
      .reverse()
      .filter((item) => item.role === 'user' || item.role === 'agent')
      .slice(-MAX_AGENT_HISTORY_TURNS)
      .map((item) => ({
        role: item.role === 'agent' ? 'assistant' : 'user',
        content: item.text,
      }));
    if (pendingUserMessage) turns.push({ role: 'user', content: pendingUserMessage });
    return turns;
  }

  function speak(text) {
    const sentences = text.match(/[^.!?]+[.!?]+|\S+$/g) || [text];
    let i = 0;
    (function next() {
      if (i >= sentences.length) return;
      const utterance = new SpeechSynthesisUtterance(sentences[i++]);
      utterance.onend = next;
      utterance.onerror = next;
      window.speechSynthesis.speak(utterance);
    })();
  }

  async function selectAttachment(file) {
    setAttachmentError(null);
    try {
      const previewUrl = await fileToDataUrl(file);
      setAttachment({ file, previewUrl, description: null });
    } catch (e) {
      setAttachmentError(e.message);
    }
  }

  async function generateForMessage(messageId, request, regenerate = false) {
    const nextRequest = { ...request, seed: regenerate ? request.seed + 1 : request.seed };
    updateMessage(messageId, { imageBusy: true, imageError: null, imageRequest: nextRequest });
    try {
      const generated = await generateReuseImage(nextRequest);
      updateMessage(messageId, { imageBusy: false, generatedImage: generated, imageError: null });
      if (nextRequest.image && onRoomContext) {
        onRoomContext({
          ...(roomContext || {}),
          file: nextRequest.image,
          previewUrl: nextRequest.sourcePreview,
          description: nextRequest.currentUse,
          proposedUse: nextRequest.proposedUse,
          generatedImage: generated,
        });
      }
    } catch (e) {
      updateMessage(messageId, { imageBusy: false, imageError: e.message });
    }
  }

  async function send(rawMessage, { directVisualization = false } = {}) {
    const message = rawMessage.trim();
    if (!message) return;
    const turnAttachment = attachment;
    setAttachment(null);
    setAttachmentError(null);
    const userId = nextId();
    setLog((items) => [{ id: userId, role: 'user', text: message, previewUrl: turnAttachment?.previewUrl }, ...items]);
    setBusy(true);

    let description = turnAttachment?.description || null;
    let analysisError = null;
    if (turnAttachment?.file && !description) {
      try {
        const analysis = await analyzeImage(turnAttachment.file);
        description = analysis.description;
      } catch (e) {
        analysisError = `Room analysis unavailable: ${e.message}`;
      }
    }

    const reusablePhoto = turnAttachment?.file || roomContext?.file || null;
    const sourcePreview = turnAttachment?.previewUrl || roomContext?.previewUrl || null;
    const shouldGenerate = wantsReuseImage(message, Boolean(reusablePhoto));
    const currentUse = (description || roomContext?.description || 'an uploaded interior room').slice(0, 420);
    const agentMessage = turnAttachment
      ? `The user uploaded a room photo. ${description ? `Observed vision description: ${description}` : 'The vision description is unavailable, so do not invent room details.'} User request: ${message}`
      : shouldGenerate && roomContext?.description
        ? `A previously uploaded room is available. Observed vision description: ${roomContext.description}. User request: ${message}`
        : message;

    let agentData = null;
    let agentText = reusablePhoto
      ? 'Rendering the attached space as an adaptive-reuse concept in this conversation.'
      : 'Rendering an adaptive-reuse concept in this conversation.';
    if (!directVisualization) {
      try {
        agentData = await askAgent(agentMessage, buildAgentHistory(agentMessage));
        agentText = agentData.answer;
        speak(agentText);
      } catch (e) {
        agentText = `Agent response unavailable: ${e.message}`;
      }
    }

    const agentId = nextId();
    const imageRequest = shouldGenerate ? {
      building: building?.n || building?.a || 'the selected building',
      currentUse,
      proposedUse: proposedUseFrom(message, roomContext?.proposedUse),
      image: reusablePhoto,
      sourcePreview,
      seed: roomContext?.generatedImage?.seed ?? 7,
      extras: 'Preserve visible structural geometry when a room reference is attached. This is a conceptual adaptive-reuse visualization, not a feasibility conclusion.',
    } : null;
    setLog((items) => [{
      id: agentId,
      role: 'agent',
      text: agentText,
      trace: agentData?.tool_trace,
      analysisError,
      imageRequest,
      imageBusy: Boolean(imageRequest),
    }, ...items]);
    setBusy(false);

    if (turnAttachment?.file && onRoomContext) {
      onRoomContext({
        file: turnAttachment.file,
        previewUrl: turnAttachment.previewUrl,
        description: currentUse,
        proposedUse: imageRequest?.proposedUse || '',
        generatedImage: null,
      });
    }
    if (imageRequest) await generateForMessage(agentId, imageRequest);
  }

  async function startRecording(event) {
    if (recording || mediaRecorderRef.current?.state === 'recording') return;
    event?.preventDefault();
    holdingToRecordRef.current = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!holdingToRecordRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        setAttachmentError('Hold the microphone button while permission is granted, then speak for at least one second.');
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
      recorder.onerror = () => setAttachmentError('Microphone recording failed. Check the browser microphone permission and try again.');
      recorder.onstop = async () => {
        const elapsed = performance.now() - recordingStartedAtRef.current;
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || supportedMime || 'audio/webm' });
        mediaRecorderRef.current = null;
        if (elapsed < 700 || blob.size < 1024) {
          setAttachmentError('Recording was too short. Hold the microphone button for at least one second.');
          return;
        }
        setBusy(true);
        try {
          const transcription = await transcribe(blob);
          if (transcription.text) await send(transcription.text);
        } catch (e) {
          setLog((items) => [{ id: nextId(), role: 'agent', text: 'Transcription error: ' + e.message }, ...items]);
        }
        setBusy(false);
      };
      recorder.start(250);
      mediaRecorderRef.current = recorder;
      recordingStartedAtRef.current = performance.now();
      setRecording(true);
    } catch (e) {
      holdingToRecordRef.current = false;
      setAttachmentError('Mic error: ' + e.message);
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

  return (
    <div className="screen chat-screen">
      <header className="screen-header"><Logo /><div className={`live-status ${agentHealth && agentHealth.agent_ready !== true ? 'offline' : ''}`}><span /> {agentHealth?.agent_ready === true ? 'AGENT ONLINE' : agentHealth ? 'AGENT DEGRADED' : 'CHECKING AGENT'}</div></header>
      <div className="eyebrow"><span>AI</span> BUILDING INTELLIGENCE</div>
      <h1 className="headline">Ask the building.<br /><em>See its future.</em></h1>
      <p className="subhead">Attach a room and ask to reuse or repurpose it. Recast grounds the answer, then triggers a structure-aware future render.</p>

      <div className="chat-log">
        {log.length === 0 && (
          <div className="chat-empty">
            <div className="agent-core" aria-hidden="true"><i /><i /><span /></div>
            <p>What do you want to understand?</p>
            <div className="prompt-grid">
              <button onClick={() => setInput('Which buildings need attention first?')}>Priority buildings</button>
              <button onClick={() => setInput('What could this building become?')}>Reuse potential</button>
              <button disabled={busy} onClick={() => send(VISUALIZE_REUSE_PROMPT, { directVisualization: true })}>Visualize reuse</button>
            </div>
          </div>
        )}
        {busy && (
          <div className="chat-msg agent chat-pipeline" role="status" aria-live="polite">
            <span className="scanner" />
            <span>Recast is evaluating the reuse and preparing the visual pipeline</span>
          </div>
        )}
        {log.map((message) => (
          <div key={message.id} className={`chat-msg ${message.role}`}>
            {message.previewUrl && <img className="chat-room-preview" src={message.previewUrl} alt="Room attached to message" />}
            <div>{message.text}</div>
            {message.trace?.length > 0 && <div className="tool-trace">tools: {message.trace.map((tool) => tool.tool).join(', ')}</div>}
            {message.analysisError && <div className="system-alert compact"><span>!</span> {message.analysisError}</div>}
            {message.imageRequest && (
              <ReuseImagePanel
                result={message.generatedImage}
                sourcePreview={message.imageRequest.sourcePreview}
                busy={message.imageBusy}
                error={message.imageError}
                proposedUse={message.imageRequest.proposedUse}
                onGenerate={() => generateForMessage(message.id, message.imageRequest, Boolean(message.generatedImage))}
              />
            )}
          </div>
        ))}
      </div>

      {attachment && (
        <div className="chat-attachment">
          <img src={attachment.previewUrl} alt="Room ready to attach" />
          <div><strong>Room attached</strong><span>Ask to reuse, convert, or visualize it.</span></div>
          <button onClick={() => setAttachment(null)} aria-label="Remove attached room">×</button>
        </div>
      )}
      {attachmentError && <div className="system-alert compact"><span>!</span> {attachmentError}</div>}

      <div className="chat-input-row">
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          hidden
          onChange={(event) => {
            if (event.target.files[0]) selectAttachment(event.target.files[0]);
            event.target.value = '';
          }}
        />
        <button className="attach-btn" onClick={() => fileInputRef.current?.click()} aria-label="Attach a room photo"><span aria-hidden="true">＋</span></button>
        <button className={`talk-btn ${recording ? 'recording' : ''}`}
          onPointerDown={startRecording} onPointerUp={stopRecording}
          onPointerCancel={stopRecording} aria-label="Hold to talk"><span aria-hidden="true">◉</span></button>
        <textarea rows={1} placeholder="Ask about or repurpose this space…" aria-label="Message Recast" value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); if (input.trim()) { send(input); setInput(''); } } }} />
        <button className="btn primary send-btn" disabled={busy || !input.trim()} onClick={() => { send(input); setInput(''); }}>Send</button>
      </div>
    </div>
  );
}
