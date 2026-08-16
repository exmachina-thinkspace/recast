import { useState } from 'react';
import MapScreen from './screens/MapScreen.jsx';
import ScoreScreen from './screens/ScoreScreen.jsx';
import CaptureScreen from './screens/CaptureScreen.jsx';
import PossibilityScreen from './screens/PossibilityScreen.jsx';
import UpdatedScoreScreen from './screens/UpdatedScoreScreen.jsx';
import ChatScreen from './screens/ChatScreen.jsx';

function App() {
  const [tab, setTab] = useState('walkthrough');
  const [step, setStep] = useState('map');
  const [buildingId, setBuildingId] = useState(null);
  const [selectedBuilding, setSelectedBuilding] = useState(null);
  const [beforeScore, setBeforeScore] = useState(null);
  const [roomContext, setRoomContext] = useState(null);

  function selectBuilding(i) {
    setBuildingId(i);
    setSelectedBuilding(null);
    setRoomContext(null);
    setStep('score');
  }

  let content;
  if (tab === 'chat') {
    content = <ChatScreen building={selectedBuilding} roomContext={roomContext} onRoomContext={setRoomContext} />;
  } else {
    switch (step) {
      case 'map':
        content = <MapScreen onSelectBuilding={selectBuilding} />;
        break;
      case 'score':
        content = <ScoreScreen buildingId={buildingId} onNext={() => setStep('capture')} onScoreLoaded={setBeforeScore} onBuildingLoaded={setSelectedBuilding} />;
        break;
      case 'capture':
        content = <CaptureScreen building={selectedBuilding} roomContext={roomContext} onRoomContext={setRoomContext} onNext={() => setStep('possibility')} />;
        break;
      case 'possibility':
        content = <PossibilityScreen building={selectedBuilding} roomContext={roomContext} onRoomContext={setRoomContext} onNext={() => setStep('updated')} />;
        break;
      case 'updated':
        content = <UpdatedScoreScreen buildingId={buildingId} beforeScore={beforeScore} onBackToMap={() => setStep('map')} />;
        break;
      default:
        content = null;
    }
  }

  return (
    <main className="app-shell">
      <div className="ambient-orb ambient-orb--cyan" aria-hidden="true" />
      <div className="ambient-orb ambient-orb--violet" aria-hidden="true" />
      <div className="app-surface">
        <div className="app-content">{content}</div>
      </div>
      <div className="tabbar">
        <button className={tab === 'walkthrough' ? 'active' : ''} onClick={() => setTab('walkthrough')}>
          <span className="tab-icon tab-icon--twin" aria-hidden="true"><i /></span>
          <span>Digital twin</span>
        </button>
        <button className={tab === 'chat' ? 'active' : ''} onClick={() => setTab('chat')}>
          <span className="tab-icon tab-icon--agent" aria-hidden="true"><i /></span>
          <span>Ask Recast</span>
        </button>
      </div>
    </main>
  );
}

export default App;
