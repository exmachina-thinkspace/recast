// API base URLs. Everything here calls the box's already-running services
// -- this app never re-implements scoring/search logic, only consumes it.
// Uses whatever hostname this app itself was loaded from (localhost via
// the SSH tunnel, or the box's LAN IP if opened that way) so every link
// stays consistent -- hardcoding the LAN IP directly is what broke the
// city-view link (Chrome couldn't route to it, same issue the voice-agent
// tunnel already fixed).
const DEFAULT_HOST = window.location.hostname;
const DEFAULT_ORIGIN = window.location.origin;

function trimTrailingSlash(value) {
  return value ? value.replace(/\/+$/, '') : value;
}

export const API = {
  buildings: trimTrailingSlash(import.meta.env.VITE_BUILDINGS_API_URL) || `http://${DEFAULT_HOST}:8900`,
  agent: trimTrailingSlash(import.meta.env.VITE_AGENT_API_URL) || `http://${DEFAULT_HOST}:8601`,
  lensBridge: trimTrailingSlash(import.meta.env.VITE_LENS_BRIDGE_URL) || (window.location.protocol === 'https:' ? '' : `http://${DEFAULT_HOST}:8910`),
};

export const CITY_VIEW_URL = trimTrailingSlash(import.meta.env.VITE_CITY_VIEW_URL) || `http://${DEFAULT_HOST}:8700/seattle-office-vitals-3d.html`;
export const FRONTEND_ORIGIN = DEFAULT_ORIGIN;

export async function getBuildings() {
  const res = await fetch(`${API.buildings}/api/buildings`);
  if (!res.ok) throw new Error('failed to load buildings');
  return res.json();
}

export async function getBuildingDetail(i) {
  const res = await fetch(`${API.buildings}/api/buildings/${i}`);
  if (!res.ok) throw new Error('failed to load building detail');
  return res.json();
}

export async function askAgent(message) {
  const res = await fetch(`${API.agent}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data; // { answer, tool_trace }
}

export async function transcribe(blob) {
  const res = await fetch(`${API.agent}/transcribe`, { method: 'POST', body: blob });
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data; // { text, language, elapsed_s }
}

export async function analyzeImage(file) {
  const res = await fetch(`${API.agent}/analyze-image`, { method: 'POST', body: file });
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data; // { description, source }
}

export async function getLensBridgeHealth() {
  const res = await fetch(`${API.lensBridge}/health`);
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'lens bridge unavailable');
  return data;
}

export async function sendLensFrame(blob, metadata = {}) {
  const res = await fetch(`${API.lensBridge}/api/recast-lens/frame`, {
    method: 'POST',
    headers: {
      'X-Recast-Session': metadata.sessionId || '',
      'X-Recast-Device': metadata.deviceLabel || '',
      'X-Recast-Source': 'recast-frontend-browser-camera',
    },
    body: blob,
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'frame upload failed');
  return data;
}

export async function interpretLensFrame(question = 'What am I seeing in this Recast Lens frame?') {
  const res = await fetch(`${API.lensBridge}/api/recast-lens/interpret`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'vision interpretation failed');
  return data;
}

export async function detectLensObjects() {
  const res = await fetch(`${API.lensBridge}/api/recast-lens/detect-objects`, {
    method: 'POST',
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'object detection failed');
  return data;
}
