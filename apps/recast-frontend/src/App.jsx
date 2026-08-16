import { useState } from 'react';
import MapScreen from './screens/MapScreen.jsx';
import ScoreScreen from './screens/ScoreScreen.jsx';
import CaptureScreen from './screens/CaptureScreen.jsx';
import PossibilityScreen from './screens/PossibilityScreen.jsx';
import UpdatedScoreScreen from './screens/UpdatedScoreScreen.jsx';
import ChatScreen from './screens/ChatScreen.jsx';

const FLOW = ['map', 'score', 'capture', 'possibility', 'updated'];

function App() {
  const [tab, setTab] = useState('walkthrough');
  const [step, setStep] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return FLOW.includes(params.get('step')) ? params.get('step') : 'map';
  });
  const [buildingId, setBuildingId] = useState(null);
  const [beforeScore, setBeforeScore] = useState(null);

  function selectBuilding(i) {
    setBuildingId(i);
    setStep('score');
  }

  let content;
  if (tab === 'chat') {
    content = <ChatScreen />;
  } else {
    switch (step) {
      case 'map':
        content = <MapScreen onSelectBuilding={selectBuilding} />;
        break;
      case 'score':
        content = <ScoreScreen buildingId={buildingId} onNext={() => setStep('capture')} onScoreLoaded={setBeforeScore} />;
        break;
      case 'capture':
        content = <CaptureScreen onNext={() => setStep('possibility')} />;
        break;
      case 'possibility':
        content = <PossibilityScreen onNext={() => setStep('updated')} />;
        break;
      case 'updated':
        content = <UpdatedScoreScreen buildingId={buildingId} beforeScore={beforeScore} onBackToMap={() => setStep('map')} />;
        break;
      default:
        content = null;
    }
  }

  return (
    <>
      <div style={{ flex: 1 }}>{content}</div>
      <div className="tabbar">
        <button className={tab === 'walkthrough' ? 'active' : ''} onClick={() => setTab('walkthrough')}>🏢<span>Walkthrough</span></button>
        <button className={tab === 'chat' ? 'active' : ''} onClick={() => setTab('chat')}>🤖<span>Ask agent</span></button>
      </div>
    </>
  );
}

export default App;
