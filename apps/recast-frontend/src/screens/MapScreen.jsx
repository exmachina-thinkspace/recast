import { useEffect, useState } from 'react';
import { Logo, scoreColor } from '../components.jsx';
import { getBuildings, CITY_VIEW_URL } from '../api.js';

// Flat, dependency-free "map" -- positions pins by normalizing real lat/lon
// into the bounding box of our building set. No tile provider/API key
// needed, matching the mockup's clean flat style without adding a mapping
// library dependency. A link to the full photorealistic 3D view (already
// built, city-view-3d) is offered for the fuller experience.
const BOUNDS = { latMin: 47.593, latMax: 47.656, lonMin: -122.368, lonMax: -122.318 };

function toXY(la, lo) {
  const x = ((lo - BOUNDS.lonMin) / (BOUNDS.lonMax - BOUNDS.lonMin)) * 100;
  const y = (1 - (la - BOUNDS.latMin) / (BOUNDS.latMax - BOUNDS.latMin)) * 100;
  return { x: Math.min(98, Math.max(2, x)), y: Math.min(96, Math.max(4, y)) };
}

export default function MapScreen({ onSelectBuilding }) {
  const [buildings, setBuildings] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getBuildings().then(setBuildings).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="screen">
      <Logo />
      <h1 className="headline">The Health Score for Every Building.</h1>
      <p className="subhead">Every building has a score based on real data — public records,
        satellite imagery, and potentially onsite visits.</p>

      {error && <p className="subhead" style={{ color: '#c0392b' }}>Couldn't load buildings: {error}</p>}

      <div className="map-wrap">
        <div style={{
          position: 'absolute', inset: 0,
          background: 'repeating-linear-gradient(0deg, #eef1f5, #eef1f5 39px, #e3e8ee 40px), repeating-linear-gradient(90deg, transparent, transparent 39px, #e3e8ee 40px)',
        }} />
        {buildings && buildings.filter(b => b.has_score).map((b) => {
          const { x, y } = toXY(b.la, b.lo);
          return (
            <div
              key={b.i}
              className="map-pin"
              style={{ left: x + '%', top: y + '%', background: scoreColor(b.bhi) }}
              title={b.name}
              onClick={() => onSelectBuilding(b.i)}
            >
              {Math.round(b.bhi)}
            </div>
          );
        })}
        {!buildings && !error && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#9aa3b0', fontWeight: 700 }}>
            Loading real scores…
          </div>
        )}
      </div>
      <div className="map-hint">👆 Tap a building to explore it</div>
      <a className="linklike" href={CITY_VIEW_URL} target="_blank" rel="noreferrer" style={{ textAlign: 'center' }}>
        Open the full photorealistic 3D city view →
      </a>
    </div>
  );
}
