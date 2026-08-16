import { useRef, useState } from 'react';
import { Logo, Pill } from '../components.jsx';
import { askAgent, transcribe } from '../api.js';

export default function ChatScreen() {
  const [log, setLog] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);

  function speak(text) {
    const sentences = text.match(/[^.!?]+[.!?]+|\S+$/g) || [text];
    let i = 0;
    (function next() {
      if (i >= sentences.length) return;
      const u = new SpeechSynthesisUtterance(sentences[i++]);
      u.onend = next; u.onerror = next;
      window.speechSynthesis.speak(u);
    })();
  }

  async function send(message) {
    setLog((l) => [{ role: 'user', text: message }, ...l]);
    setBusy(true);
    try {
      const data = await askAgent(message);
      setLog((l) => [{ role: 'agent', text: data.answer, trace: data.tool_trace }, ...l]);
      speak(data.answer);
    } catch (e) {
      setLog((l) => [{ role: 'agent', text: 'Error: ' + e.message }, ...l]);
    }
    setBusy(false);
  }

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunksRef.current = [];
      const mr = new MediaRecorder(stream);
      mr.ondataavailable = (e) => chunksRef.current.push(e.data);
      mr.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        setBusy(true);
        try {
          const t = await transcribe(blob);
          if (t.text) await send(t.text);
        } catch (e) { console.error(e); }
        setBusy(false);
      };
      mr.start();
      mediaRecorderRef.current = mr;
      setRecording(true);
    } catch (e) { alert('mic error: ' + e.message); }
  }
  function stopRecording() {
    const mr = mediaRecorderRef.current;
    if (mr && mr.state !== 'inactive') { mr.stop(); mr.stream.getTracks().forEach(t => t.stop()); }
    setRecording(false);
  }

  return (
    <div className="screen" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Logo />
      <Pill tone="blue">🤖 Ask the agent</Pill>
      <p className="subhead">Real tool-calling agent — BHI scores, live crime/permit/business data, camera descriptions, room reuse. Answers cite real tool calls.</p>

      <div className="chat-log">
        {log.map((m, idx) => (
          <div key={idx} className={`chat-msg ${m.role}`}>
            {m.text}
            {m.trace && m.trace.length > 0 && <div className="tool-trace">tools: {m.trace.map(t => t.tool).join(', ')}</div>}
          </div>
        ))}
      </div>

      <div className="chat-input-row">
        <button className={`talk-btn ${recording ? 'recording' : ''}`}
          onMouseDown={startRecording} onMouseUp={stopRecording}
          onTouchStart={startRecording} onTouchEnd={stopRecording}>🎙️</button>
        <textarea rows={1} placeholder="Type or hold the mic…" value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (input.trim()) { send(input.trim()); setInput(''); } } }} />
        <button className="btn primary" disabled={busy || !input.trim()} onClick={() => { send(input.trim()); setInput(''); }}>Send</button>
      </div>
    </div>
  );
}
