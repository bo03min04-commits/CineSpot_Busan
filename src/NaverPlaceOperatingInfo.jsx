import { useEffect, useState } from 'react';
import './NaverPlaceOperatingInfo.css';
import './OperatingInfoPresentation.css';
import { operatingInfoView, phoneHref, safeHttpLink } from './operatingInfoView';

const API = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
let editorKey = '';

async function post(path, body, signal, token) {
  const response = await fetch(`${API}/api/naver-operating-info/${path}`, { method: 'POST', signal, headers: { 'Content-Type': 'application/json', ...(token ? { 'X-Operating-Edit-Key': token } : {}) }, body: JSON.stringify(body) });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '서버 응답을 확인해 주세요.');
  return value;
}

export default function NaverPlaceOperatingInfo({ place }) {
  const isParking = place.kind === 'parking' || String(place.id).startsWith('parking-');
  const payload = { kind: isParking ? 'parking' : (place.kind || ''), id: String(place.id).replace(/^(food|spot|parking)-/, ''), name: place.name, address: place.address || '', phone: place.phone || '' };
  const identity = JSON.stringify(payload);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [url, setUrl] = useState('');
  const [key, setKey] = useState(editorKey);
  const [saving, setSaving] = useState(false);
  useEffect(() => { setResult(null); }, [identity]);
  useEffect(() => {
    let disposed = false, controller, timer, timeout;
    let forceRefresh = retry > 0;
    setError('');
    async function load() {
      if (!payload.address.trim()) { setError('장소 주소가 없어 네이버 자료를 연결할 수 없습니다.'); return; }
      controller = new AbortController(); timeout = setTimeout(() => controller.abort(), 15000);
      let pending = false;
      try {
        const data = await post('lookup', { ...JSON.parse(identity), force_refresh: forceRefresh }, controller.signal);
        if (data.data && !Array.isArray(data.data.weekly_hours)) throw new Error('시간표 형식을 확인해 주세요.');
        forceRefresh = false;
        if (!disposed) { setResult(data); setError(''); pending = data.pending; }
      } catch { if (!disposed) setError('운영정보 서버에 연결하지 못했습니다. 기존 시간표가 있으면 유지합니다.'); }
      finally { clearTimeout(timeout); if (!disposed) timer = setTimeout(load, pending ? 5000 : 30000); }
    }
    load();
    return () => { disposed = true; clearTimeout(timer); clearTimeout(timeout); controller?.abort(); };
  }, [identity, retry]);
  async function link() {
    setSaving(true); setError('');
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 15000);
    try {
      await post('link', { ...payload, url: url.trim() }, controller.signal, key.trim());
      editorKey = key.trim(); setRetry((value) => value + 1);
    } catch (err) { setError(err.name === 'AbortError' ? '서버 응답 시간이 초과되었습니다.' : err.message); }
    finally { clearTimeout(timeout); setSaving(false); }
  }
  const info = result?.data;
  const view = operatingInfoView(result, error, isParking ? 'parking' : '');
  const rows = new Map((info?.weekly_hours || []).map(row => [row.day, row]));
  const checkedDate = info?.checked_at ? new Date(info.checked_at) : null;
  const stamp = checkedDate && Number.isFinite(checkedDate.getTime()) ? checkedDate.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '';
  const telephone = phoneHref(place.phone);
  const mapLink = safeHttpLink(place.url) || `https://map.kakao.com/link/search/${encodeURIComponent(place.name || '')}`;
  return <section className="naver-operating-info" aria-label={`${place.name} 운영정보`}>
    <div className="nhi-heading"><strong>운영정보</strong><span className={`nhi-status nhi-${view.status}`}>{view.badge}</span></div>
    {view.hasSchedule ? <>
      <table className="nhi-table"><thead><tr><th>요일</th><th>영업시간</th><th>휴게시간</th></tr></thead><tbody>
        {[...'월화수목금토일'].map(day => { const row = rows.get(day); return <tr key={day}><th scope="row">{day}요일</th><td>{row?.closed ? '휴무' : row?.hours?.join(' / ') || '미확인'}</td><td>{row?.closed ? '—' : row?.breaks?.join(' / ') || '미확인'}</td></tr>; })}
      </tbody></table>
      <p className="nhi-meta">휴무일: {info.closed_days?.length ? info.closed_days.map(day => `${day}요일`).join(', ') : '별도 안내 미확인'}</p>
      {!info.complete_week && <p className="nhi-meta">일부 요일의 정보만 확인됐습니다. 방문 전 매장에 문의해 주세요.</p>}
      <p className="nhip-footnote">임시휴무·공휴일·휴게시간이 달라질 수 있습니다. 시간표 기준 영업 상태는 예상입니다.</p>
      {stamp && <p className="nhi-meta">확인 {stamp}</p>}
    </> : <div className="nhip-contact">
      <p>{view.message}</p>
      {view.status !== 'permanently_closed' && <p className="nhip-footnote">정보를 확인하지 못한 것이며 폐업을 의미하지 않습니다.</p>}
    </div>}
    <div className="nhip-actions">
      {telephone && <a href={telephone}>전화로 문의</a>}
      <a href={mapLink} target="_blank" rel="noreferrer">지도에서 확인 ↗</a>
    </div>
    <details className="nhip-manager"><summary>정보 관리</summary>
      {result?.pending && <p className="nhi-meta" role="status">운영정보 업데이트가 대기 중이거나 진행 중입니다.</p>}
      {result?.message && <p className="nhi-meta">최근 조회 기록: {result.message}</p>}
      {error && <p role="alert">{error}</p>}
      {view.suspicious && <p className="nhi-meta">저장된 자료의 모든 요일이 휴무로 표시돼 시간표 노출을 보류했습니다. 원본 확인이 필요합니다.</p>}
      {!isParking && <button type="button" className="nhi-refresh" onClick={() => setRetry(value => value + 1)}>운영정보 다시 확인</button>}
      <div className="nhi-admin">
        <p>관리자는 네이버 장소 주소를 연결하거나 보정할 수 있습니다.</p>
        <label>네이버 장소 주소<input value={url} onChange={event => setUrl(event.target.value)} placeholder="https://naver.me/..." maxLength={500}/></label>
        <label>운영정보 편집키<input type="password" autoComplete="off" value={key} onChange={event => setKey(event.target.value)}/></label>
        <button type="button" onClick={link} disabled={saving || !url.trim() || !key.trim()}>{saving ? '저장 중…' : '주소 저장'}</button>
      </div>
    </details>
  </section>;
}
