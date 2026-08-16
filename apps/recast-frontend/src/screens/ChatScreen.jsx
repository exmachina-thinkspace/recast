import { useEffect, useRef, useState } from 'react';
import { Logo, ReuseImagePanel } from '../components.jsx';
import { API, analyzeImage, askAgent, fileToDataUrl, generateReuseImage, getAgentHealth, getFloorplan, getReuseDetail, transcribe } from '../api.js';

const VISUALIZE_REUSE_PROMPT = 'Generate an image of this room repurposed as a community health clinic';

// Friendly label -> real candidate_use string the trajectory-engine actually
// screens against (see build_trajectory_input.py) -> a visually concrete
// description for the image generator. The bare candidate_use string
// ("multifamily/residential") is too abstract for FLUX to render
// recognizably -- it needs actual furnishings/layout cues to look like the
// use it's supposed to represent.
const REUSE_CHOICES = [
  { label: 'Office', value: 'office (as-is)',
    imagePrompt: 'a modern open-plan office with desks, task chairs, monitors, and a small meeting area' },
  { label: 'Housing', value: 'multifamily/residential',
    imagePrompt: 'a multifamily residential apartment unit, with a living room seating area, a bed and bedroom furnishings visible, and a small kitchenette' },
  { label: 'Medical / Clinic', value: 'medical/clinic',
    imagePrompt: 'an outpatient medical clinic, with an exam table, medical cabinetry, a reception desk, and clinical signage' },
  { label: 'School', value: 'school/classroom',
    imagePrompt: 'a school classroom, with student desks in rows, a whiteboard, and educational posters on the wall' },
  { label: 'Retail', value: 'retail/mall',
    imagePrompt: 'a retail storefront, with product display shelving, clothing racks or merchandise tables, and a checkout counter' },
];

const STATUS_META = {
  KEEP_FOR_DUE_DILIGENCE: { label: 'Worth pursuing', tone: 'good' },
  CONDITIONAL_DUE_DILIGENCE: { label: 'Conditional', tone: 'conditional' },
  INSUFFICIENT_EVIDENCE: { label: 'Not enough evidence yet', tone: 'unknown' },
  SCREEN_OUT: { label: 'Ruled out', tone: 'out' },
};

function fitTone(tier) {
  if (tier === 'pass') return 'good';
  if (tier === 'conditional') return 'conditional';
  if (tier === 'fail') return 'out';
  return 'unknown';
}

function classifyIntent(message) {
  const text = message.toLowerCase();
  const generationIntent = /\b(generate|create|show me|regenerate)\b/.test(text);
  // "image"/"picture" alone are ambiguous -- "I have this room image" is
  // describing an attachment, not asking to generate one. Only count as
  // visual intent if paired with a generation verb, or if the word itself
  // is unambiguous (render/visualize/concept art are rarely used to
  // describe an existing photo).
  const unambiguousVisual = /\b(render|visuali[sz]e|concept art)\b/.test(text);
  const ambiguousVisualWord = /\b(image|picture)\b/.test(text);
  return {
    visualIntent: unambiguousVisual || (generationIntent && ambiguousVisualWord),
    generationIntent,
    reuseIntent: /\b(reuse|repurpose|convert|reimagine|re-imagine|turn|become|future use)\b/.test(text),
  };
}

// Explicit ask to generate/visualize a specific use -> generate immediately, as before.
function wantsReuseImage(message) {
  const { visualIntent, generationIntent, reuseIntent } = classifyIntent(message);
  return visualIntent || (generationIntent && reuseIntent);
}

// Vague "give me ideas how to reuse this" with a room attached, but no
// specific use named and no explicit generate/visualize ask -- show real
// screened candidates to pick from instead of guessing one and generating.
function wantsReuseIdeas(message, hasRoomPhoto) {
  const { visualIntent, generationIntent, reuseIntent } = classifyIntent(message);
  return Boolean(hasRoomPhoto) && reuseIntent && !visualIntent && !generationIntent;
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
  const [log, setLog] = useState([]);
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
  const messageIdRef = useRef(0);

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

  function nextId() {
    messageIdRef.current += 1;
    return messageIdRef.current;
  }

  function updateMessage(id, patch) {
    setLog((items) => items.map((item) => item.id === id ? { ...item, ...patch } : item));
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
    const wantsIdeas = wantsReuseIdeas(message, Boolean(reusablePhoto));
    const wantsImage = directVisualization || wantsReuseImage(message);
    const currentUse = (description || roomContext?.description || 'an uploaded interior room').slice(0, 420);
    const agentMessage = turnAttachment
      ? `The user uploaded a room photo. ${description ? `Observed vision description: ${description}` : 'The vision description is unavailable, so do not invent room details.'} User request: ${message}`
      : wantsImage && roomContext?.description
        ? `A previously uploaded room is available. Observed vision description: ${roomContext.description}. User request: ${message}`
        : message;

    let agentData = null;
    let agentText = reusablePhoto
      ? 'Rendering the attached space as an adaptive-reuse concept in this conversation.'
      : 'Rendering an adaptive-reuse concept in this conversation.';
    if (!directVisualization) {
      try {
        agentData = await askAgent(agentMessage);
        agentText = agentData.answer;
        speak(agentText);
      } catch (e) {
        agentText = `Agent response unavailable: ${e.message}`;
      }
    }

    // If the agent looked up a real building (get_bhi), surface its BHI +
    // real measured floor plan inline instead of leaving it to text alone.
    let buildingContext = null;
    const bhiCall = agentData?.tool_trace?.find((t) => t.tool === 'get_bhi' && !t.result?.error);
    if (bhiCall?.result?.floorplan_url) {
      try {
        const plan = await getFloorplan();
        buildingContext = { bhi: bhiCall.result, floorplan: plan };
      } catch {
        buildingContext = { bhi: bhiCall.result, floorplan: null };
      }
    } else if (bhiCall?.result?.bhi != null) {
      buildingContext = { bhi: bhiCall.result, floorplan: null };
    }

    // Reuse base data needed to actually generate an image later, whether
    // that happens immediately (explicit ask) or after picking a screened
    // candidate (vague "give me ideas" ask).
    const reuseBase = {
      building: building?.n || building?.a || 'the selected building',
      currentUse,
      image: reusablePhoto,
      sourcePreview,
      seed: roomContext?.generatedImage?.seed ?? 7,
      extras: 'Preserve visible structural geometry when a room reference is attached. This is a conceptual adaptive-reuse visualization, not a feasibility conclusion.',
    };

    const agentId = nextId();
    const imageRequest = (wantsImage && !wantsIdeas) ? {
      ...reuseBase,
      proposedUse: proposedUseFrom(message, roomContext?.proposedUse),
    } : null;
    setLog((items) => [{
      id: agentId,
      role: 'agent',
      text: agentText,
      trace: agentData?.tool_trace,
      analysisError,
      buildingContext,
      // Ask the user to pick a use first -- nothing is evaluated or
      // generated until they choose. See evaluateReuseChoice below.
      awaitingReuseChoice: wantsIdeas,
      reuseChoice: null,
      reuseBase,
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

  // User picked one use from the dropdown. Now, and only now: (1) fact-check
  // it against real screened data, (2) ask the model for a grounded plain-
  // English why/why-not narration of those same facts (best-effort -- the
  // structured fact-check above already stands on its own if this fails),
  // and (3) generate a concept image for that specific use.
  async function evaluateReuseChoice(messageId, reuseBase, candidateUse) {
    updateMessage(messageId, {
      awaitingReuseChoice: false,
      reuseChoice: { selectedUse: candidateUse, loading: true, detail: null, narration: null, narrationError: null },
    });

    let detail = null;
    try {
      detail = await getReuseDetail(candidateUse);
      updateMessage(messageId, {
        reuseChoice: { selectedUse: candidateUse, loading: false, detail, narration: null, narrationError: null },
      });
    } catch (e) {
      updateMessage(messageId, {
        reuseChoice: { selectedUse: candidateUse, loading: false, detail: null, narration: null, narrationError: e.message },
      });
      return;
    }

    // Best-effort grounded narration -- feed the model the exact structured
    // facts we just showed so it can only explain them, not invent new ones.
    askAgent(
      `The user is considering converting their space to "${candidateUse}". ` +
      `Here is the real, evidence-gated fact-check (do not add facts beyond this): ${JSON.stringify(detail)}. ` +
      `In 3-4 sentences, explain why this use does or doesn't fit well and what would need to happen next. ` +
      `Do not call any tools -- just summarize the facts given.`
    ).then((res) => {
      updateMessage(messageId, {
        reuseChoice: { selectedUse: candidateUse, loading: false, detail, narration: res.answer, narrationError: null },
      });
    }).catch((e) => {
      updateMessage(messageId, {
        reuseChoice: { selectedUse: candidateUse, loading: false, detail, narration: null, narrationError: e.message },
      });
    });

    // Feed the image generator a visually concrete description, not the
    // bare candidate_use label -- "multifamily/residential" alone gives
    // FLUX nothing to render recognizably as housing.
    const imagePrompt = REUSE_CHOICES.find((c) => c.value === candidateUse)?.imagePrompt || candidateUse;
    const request = { ...reuseBase, proposedUse: imagePrompt };
    updateMessage(messageId, { imageRequest: request });
    if (reuseBase.image && onRoomContext) {
      onRoomContext({
        file: reuseBase.image,
        previewUrl: reuseBase.sourcePreview,
        description: reuseBase.currentUse,
        proposedUse: imagePrompt,
        generatedImage: null,
      });
    }
    await generateForMessage(messageId, request);
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
      // not the user releasing early. The elapsed/size check in onstop below
      // already catches genuinely too-short recordings.
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
            {message.buildingContext && (
              <div className="building-context-card">
                <div className="building-context-score">
                  <span className="bhi-number">{message.buildingContext.bhi.bhi}</span>
                  <span className="bhi-label">BHI &middot; {Math.round((message.buildingContext.bhi.evidence_coverage || 0) * 100)}% evidence coverage</span>
                </div>
                {message.buildingContext.bhi.vitals && (
                  <div className="building-context-vitals">
                    {Object.entries(message.buildingContext.bhi.vitals).map(([key, v]) => (
                      <div key={key} className="building-vital-row">
                        <span>{key.replace(/_/g, ' ')}</span>
                        <span>{v.score}</span>
                      </div>
                    ))}
                  </div>
                )}
                {message.buildingContext.bhi.measured_floor_plan && (
                  <div className="building-context-note">
                    {message.buildingContext.bhi.measured_floor_plan.room_count} rooms measured &middot; {message.buildingContext.bhi.measured_floor_plan.total_sqft.toLocaleString()} sqft (real architectural plan)
                  </div>
                )}
                {message.buildingContext.floorplan?.levels?.length > 0 && (
                  <div className="floorplan-thumbs">
                    {message.buildingContext.floorplan.levels.map((lvl) => (
                      <a key={lvl.level} href={`${API.buildings}${lvl.image_url}`} target="_blank" rel="noreferrer">
                        <img src={`${API.buildings}${lvl.image_url}`} alt={`${lvl.level} floor plan`} />
                        <span>{lvl.level}</span>
                      </a>
                    ))}
                  </div>
                )}
              </div>
            )}
            {message.awaitingReuseChoice && (
              <div className="reuse-picker">
                <div className="reuse-picker-hint">What should this space become? Pick one for a real, fact-checked evaluation:</div>
                <select
                  className="reuse-picker-select"
                  defaultValue=""
                  onChange={(event) => {
                    if (event.target.value) evaluateReuseChoice(message.id, message.reuseBase, event.target.value);
                  }}
                >
                  <option value="" disabled>Choose a use…</option>
                  {REUSE_CHOICES.map((choice) => (
                    <option key={choice.value} value={choice.value}>{choice.label}</option>
                  ))}
                </select>
              </div>
            )}
            {message.reuseChoice && (
              <div className="reuse-detail-card">
                <div className="reuse-detail-header">
                  <span className="reuse-detail-use">
                    {REUSE_CHOICES.find((choice) => choice.value === message.reuseChoice.selectedUse)?.label || message.reuseChoice.selectedUse}
                  </span>
                  {message.reuseChoice.detail && (
                    <span className={`reuse-option-status tone-${(STATUS_META[message.reuseChoice.detail.status] || {}).tone || 'unknown'}`}>
                      {(STATUS_META[message.reuseChoice.detail.status] || {}).label || message.reuseChoice.detail.status}
                    </span>
                  )}
                </div>
                {message.reuseChoice.loading && <div className="reuse-detail-loading">Fact-checking against real building data…</div>}
                {message.reuseChoice.narrationError && !message.reuseChoice.detail && (
                  <div className="system-alert compact"><span>!</span> {message.reuseChoice.narrationError}</div>
                )}
                {message.reuseChoice.detail && (
                  <>
                    {message.reuseChoice.narration && <p className="reuse-detail-narration">{message.reuseChoice.narration}</p>}
                    <div className="reuse-detail-dims">
                      {['physical', 'regulatory', 'market', 'financial'].map((dim) => (
                        <div key={dim} className={`reuse-detail-dim tone-${fitTone(message.reuseChoice.detail[dim].tier)}`}>
                          <span className="dim-label">{dim}</span>
                          <span className="dim-tier">{message.reuseChoice.detail[dim].tier || 'unknown'}</span>
                          <span className="dim-reasoning">{message.reuseChoice.detail[dim].reasoning}</span>
                        </div>
                      ))}
                    </div>
                    {message.reuseChoice.detail.changes_needed?.length > 0 && (
                      <div className="reuse-detail-changes">
                        <div className="reuse-detail-changes-label">Changes / evidence needed:</div>
                        <ul>{message.reuseChoice.detail.changes_needed.map((item, i) => <li key={i}>{item}</li>)}</ul>
                      </div>
                    )}
                    <div className="reuse-options-caveat">{message.reuseChoice.detail.limitation}</div>
                  </>
                )}
              </div>
            )}
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
