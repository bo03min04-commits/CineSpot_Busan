import { useEffect, useRef, useState } from 'react';
import { GOOGLE_KEY, kakaoPlaceId, loadGooglePlaces, mountGoogleInfo, removeLink, writeLink } from './googlePlacesUiKit';
import './GooglePlaceOperatingInfo.css';

export default function GooglePlaceOperatingInfo({ place }) {
  const host = useRef(null);
  const shell = useRef(null);
  const [visible, setVisible] = useState(false);
  const [state, setState] = useState('idle');
  const [message, setMessage] = useState('');
  const [retry, setRetry] = useState(0);
  const identity = JSON.stringify({ id: place.id, name: place.name, address: place.address, lat: place.lat, lng: place.lng });
  useEffect(() => {
    if (!GOOGLE_KEY) return;
    if (!window.IntersectionObserver) { setVisible(true); return; }
    const observer = new window.IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: '100px' });
    if (shell.current) observer.observe(shell.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!GOOGLE_KEY || !visible) return;
    let disposed = false;
    let cleanup;
    setState('loading'); setMessage('Google에 연결하는 중입니다.');
    loadGooglePlaces().then((library) => {
      if (!disposed && host.current) cleanup = mountGoogleInfo(host.current, JSON.parse(identity), library, (status, text) => { if (!disposed) { setState(status); setMessage(text); } }, writeLink);
    }).catch(() => {
      if (!disposed) { setState('error'); setMessage('Google 연결에 실패했습니다. API 키·사용 설정과 네트워크를 확인해 주세요.'); }
    });
    return () => { disposed = true; cleanup?.(); };
  }, [identity, visible, retry]);
  function reconnect() {
    if (!removeLink(kakaoPlaceId(place))) { setMessage('브라우저의 장소 연결을 지우지 못했습니다. 저장소 설정을 확인해 주세요.'); return; }
    setRetry((value) => value + 1);
  }
  return <section className="google-place-operating" aria-label={`${place.name} Google 운영정보`} ref={shell}>
    <strong>운영정보</strong>
    {!GOOGLE_KEY && <p>Google Maps 키 설정 후 자동 조회를 사용할 수 있습니다.</p>}
    {GOOGLE_KEY && <>
      {state === 'idle' && <p>카드가 화면에 보이면 Google 운영정보를 불러옵니다.</p>}
      {message && <p className="google-operating-message" role={state === 'error' ? 'alert' : 'status'}>{message}</p>}
      <div className="google-operating-widget" ref={host}/>
      <div className="google-operating-actions">
        {state === 'error' && <button type="button" onClick={() => setRetry((value) => value + 1)}>다시 조회</button>}
        {state === 'details' && <><button type="button" onClick={() => setRetry((value) => value + 1)}>정보 새로고침</button><button type="button" onClick={reconnect}>장소 다시 검색</button></>}
        {state === 'empty' && <button type="button" onClick={() => setRetry((value) => value + 1)}>다시 검색</button>}
      </div>
      <p className="google-operating-note">Google 정보에 운영시간·휴무일이 없거나 변경 사항 반영이 늦을 수 있습니다. 영업 종료와 폐업은 구분해서 확인해 주세요.</p>
    </>}
  </section>;
}
