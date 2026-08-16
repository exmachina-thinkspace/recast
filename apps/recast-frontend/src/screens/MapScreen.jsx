import { useEffect, useMemo, useState } from 'react';
import { Logo } from '../components.jsx';
import { scoreColor } from '../scoreUtils.js';
import { getBuildings, CITY_VIEW_URL } from '../api.js';
import seattleStreetMap from '../assets/seattle-street-map.png';

// Keep the overview legible: sample eight scored buildings across the full
// north/south extent, choosing the lowest BHI in each latitude band. Every
// visible pin still opens the same real building detail flow.
function prioritySample(buildings, count = 8) {
  const scored = buildings
    .filter((building) => building.has_score && Number.isFinite(building.la) && Number.isFinite(building.lo))
    .sort((a, b) => b.la - a.la);
  if (scored.length <= count) return scored;
  const sample = [];
  for (let index = 0; index < count; index += 1) {
    const start = Math.floor((index * scored.length) / count);
    const end = Math.floor(((index + 1) * scored.length) / count);
    const band = scored.slice(start, Math.max(start + 1, end));
    sample.push(band.reduce((lowest, building) => building.bhi < lowest.bhi ? building : lowest));
  }
  return sample;
}

function positionsFor(buildings) {
  if (!buildings.length) return new Map();
  const latitudes = buildings.map((building) => building.la);
  const longitudes = buildings.map((building) => building.lo);
  const latMin = Math.min(...latitudes);
  const latMax = Math.max(...latitudes);
  const lonMin = Math.min(...longitudes);
  const lonMax = Math.max(...longitudes);
  const latSpan = latMax - latMin || 1;
  const lonSpan = lonMax - lonMin || 1;
  return new Map(buildings.map((building) => [building.i, {
    x: 24 + ((building.lo - lonMin) / lonSpan) * 52,
    y: 12 + (1 - (building.la - latMin) / latSpan) * 76,
  }]));
}

export default function MapScreen({ onSelectBuilding }) {
  const [buildings, setBuildings] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getBuildings().then(setBuildings).catch((e) => setError(e.message));
  }, []);

  const scoredCount = buildings?.filter((building) => building.has_score).length || 0;
  const visibleBuildings = useMemo(() => prioritySample(buildings || []), [buildings]);
  const positions = useMemo(() => positionsFor(visibleBuildings), [visibleBuildings]);

  return (
    <div className="screen map-screen">
      <header className="screen-header">
        <Logo />
        <div className={`live-status ${error ? 'offline' : ''}`}><span /> {error ? 'LINK OFFLINE' : 'LIVE TWIN'}</div>
      </header>

      <div className="eyebrow"><span>01</span> CITY INTELLIGENCE</div>
      <h1 className="headline map-headline">Recast: The health score<br /><em>for every building.</em></h1>
      <p className="subhead">A living health layer for every building—grounded in public records, imagery, and onsite evidence.</p>

      {error && <div className="system-alert"><span>!</span> Live building telemetry is offline: {error}</div>}

      <div className="map-wrap">
        <img className="seattle-map-image" src={seattleStreetMap} alt="Street map of Seattle and the surrounding waterways" />
        <div className="map-image-treatment" aria-hidden="true" />
        <div className="map-hud map-hud--top"><span>SEATTLE / FULL CITY</span><span>PRIORITY VIEW</span></div>
        {visibleBuildings.map((building) => {
          const { x, y } = positions.get(building.i);
          return (
            <button
              key={building.i}
              className="map-pin"
              style={{ left: x + '%', top: y + '%', '--pin-color': scoreColor(building.bhi) }}
              title={building.name}
              onClick={() => onSelectBuilding(building.i)}
              aria-label={`Explore ${building.name}, Building Health Index ${Math.round(building.bhi)}`}
            >
              <span className="pin-drop"><span className="pin-score">{Math.round(building.bhi)}</span></span>
              <span className="pin-pulse" aria-hidden="true" />
            </button>
          );
        })}
        {!buildings && !error && (
          <div className="map-loading">
            <span className="scanner" />
            Syncing city signals
          </div>
        )}
        <div className="map-hud map-hud--bottom"><span>BHI / EVIDENCE LAYER</span><span>{buildings ? `${visibleBuildings.length} OF ${scoredCount} SIGNALS` : 'SYNCING'}</span></div>
      </div>
      <div className="map-hint"><span className="gesture-dot" /> Eight priority pins · select one to inspect evidence</div>
      <a className="btn primary block" href={CITY_VIEW_URL} target="_blank" rel="noreferrer">
        <span>Enter 3D city view</span><b aria-hidden="true">↗</b>
      </a>
      <p className="truth-note">Gray does not mean healthy. It means there is not yet enough evidence.</p>
    </div>
  );
}
