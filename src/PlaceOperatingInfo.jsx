import { useState } from 'react';
import NaverPlaceOperatingInfo from './NaverPlaceOperatingInfo';
import PlaceOperatingInfoLegacy from './PlaceOperatingInfoLegacy';

export default function PlaceOperatingInfo({ place }) {
  const [manualOpen, setManualOpen] = useState(false);
  // Operating information is shown only on restaurant cards.
  const operatingKind = place?.kind || String(place?.id || '').split('-')[0];
  if (operatingKind !== 'food') return null;
  return <div className="naver-operating-wrapper">
    <NaverPlaceOperatingInfo place={place}/>
    <details onToggle={event => setManualOpen(event.currentTarget.open)}>
      <summary>직접 입력·기존 저장 정보</summary>
      {manualOpen && <PlaceOperatingInfoLegacy place={place}/>}
    </details>
  </div>;
}
