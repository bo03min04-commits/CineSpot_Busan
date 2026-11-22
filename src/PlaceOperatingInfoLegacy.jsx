import { useEffect, useRef, useState } from 'react';
import './PlaceOperatingInfo.css';

const API = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const PREFIX = 'cinespot:operating-info:v1:';
const STATES = { unknown: '영업·폐업 여부 확인 필요', operating: '운영 중 (현재 영업 여부는 시간표 확인)', temporarily_closed: '임시휴업', permanently_closed: '폐업' };
const FIELDS = ['hours', 'closedDays', 'status', 'source', 'checkedOn'];
let editKey = ''; // Kept only in page memory; not in localStorage or the URL.
const empty = () => ({ hours: '', closedDays: '', status: 'unknown', source: '', checkedOn: '' });
export function placeKey(place) { return String(place.id || '').replace(/^(food|spot|parking)-/, ''); }
function today() {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul' }).format(new Date());
}
export function validInfo(value) {
  if (!value || typeof value !== 'object' || !FIELDS.every((field) => typeof value[field] === 'string')) return false;
  const parsed = new Date(value.checkedOn + 'T00:00:00Z');
  return Object.hasOwn(STATES, value.status) && /^\d{4}-\d{2}-\d{2}$/.test(value.checkedOn) &&
    !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value.checkedOn && value.checkedOn <= today() && Boolean(value.source.trim());
}
function readLocal(key) {
  try { const value = JSON.parse(localStorage.getItem(PREFIX + key)); return validInfo(value) ? value : null; }
  catch { return null; }
}
export function chooseInfo(server, local, published) {
  if (server?.origin === 'manual') return server;
  return [local && { ...local, origin: 'local' }, server, published && { ...published, origin: 'catalog' }]
    .filter(Boolean).sort((a, b) => b.checkedOn.localeCompare(a.checkedOn))[0] || null;
}
async function request(path, body, signal, token) {
  const response = await fetch(API + '/api/place-operating-info/' + path, {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json', ...(token ? { 'X-Operating-Edit-Key': token } : {}) }, body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : '입력 내용과 백엔드 상태를 확인해 주세요.');
    error.status = response.status;
    throw error;
  }
  return data;
}

export default function PlaceOperatingInfo({ place }) {
  const key = placeKey(place);
  const [local, setLocal] = useState(() => readLocal(key));
  const [published, setPublished] = useState(null);
  const [server, setServer] = useState(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(empty);
  const [token, setToken] = useState(editKey);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const [reload, setReload] = useState(0);
  const epoch = useRef(0);
  const revision = useRef(0);
  const info = chooseInfo(server?.info, local, published);
  const payload = { id: key, name: place.name, kind: place.kind, lat: place.lat, lng: place.lng, address: place.address || '', phone: place.phone || '' };
  const identity = JSON.stringify(payload);
  useEffect(() => {
    let disposed = false;
    let active = null;
    let timeout = null;
    let timer = null;
    const version = ++epoch.current;
    revision.current = 0;
    setLocal(readLocal(key)); setPublished(null); setServer(null); setLoadError(''); setMessage('');
    const catalog = new AbortController();
    const catalogTimeout = setTimeout(() => catalog.abort(), 10000);
    fetch(`${import.meta.env.BASE_URL || '/'}place-operating-info.json`, { signal: catalog.signal })
      .then((response) => response.ok ? response.json() : null)
      .then((data) => { const value = data?.places?.[key]; if (!disposed && validInfo(value)) setPublished(value); })
      .catch(() => {}).finally(() => clearTimeout(catalogTimeout));
    async function load() {
      active = new AbortController();
      timeout = setTimeout(() => active.abort(), 45000);
      const startedRevision = revision.current;
      try {
        const data = await request('lookup', JSON.parse(identity), active.signal);
        if (!Number.isInteger(data.revision) || (data.info && !validInfo(data.info))) throw new Error('운영정보 응답 형식을 확인해 주세요.');
        // An earlier read must never overwrite a completed edit.
        if (!disposed && epoch.current === version && data.revision >= revision.current && startedRevision <= data.revision) {
          revision.current = data.revision;
          setServer(data); setLoadError('');
        }
      } catch (err) {
        if (!disposed) setLoadError(err.status === 404 ? '서버 운영정보 패치가 필요합니다.' : '서버 정보를 불러오지 못했습니다. 잠시 후 다시 확인합니다.');
      } finally {
        clearTimeout(timeout);
        if (!disposed) timer = setTimeout(load, 60000);
      }
    }
    load();
    return () => { disposed = true; epoch.current++; clearTimeout(timer); clearTimeout(timeout); clearTimeout(catalogTimeout); active?.abort(); catalog.abort(); };
  }, [identity, key, reload]);

  function cleanDraft() {
    const clean = Object.fromEntries(FIELDS.map((field) => [field, String(draft[field] || '').trim()]));
    if (!validInfo(clean)) throw new Error('정보 출처와 올바른 확인일을 입력해 주세요.');
    return clean;
  }
  async function changeShared(remove = false) {
    if (saving) return;
    const version = epoch.current;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    setError(''); setMessage(''); setSaving(true);
    try {
      if (!server) throw new Error('서버 정보를 불러온 뒤 다시 저장해 주세요.');
      if (!token.trim()) throw new Error('서버 저장용 편집키를 입력해 주세요.');
      const body = { place_id: key, expected_revision: revision.current, ...(!remove ? { info: cleanDraft() } : {}) };
      const data = await request(remove ? 'remove' : 'save', body, controller.signal, token.trim());
      if (epoch.current !== version) return;
      revision.current = data.revision; editKey = token.trim();
      setServer((old) => ({ ...old, info: data.info || null, revision: data.revision, stale: false, message: remove ? '' : '서버에 저장된 직접 입력 정보입니다.' }));
      setEditing(false); setMessage(remove ? '서버 직접 입력 정보를 삭제했습니다.' : '서버에 저장했습니다. 같은 서버를 사용하는 다른 사용자에게도 표시됩니다.');
      // Keep a local-only record untouched on server deletion.
      if (!remove) { try { localStorage.removeItem(PREFIX + key); setLocal(null); } catch {} }
      if (remove) setReload((value) => value + 1);
    } catch (err) {
      if (epoch.current === version) {
        setError(err.name === 'AbortError' ? '저장 응답을 확인하지 못했습니다. 새로고침 후 저장 여부를 확인해 주세요.' : err.message);
        // Keep the draft so it can be copied or saved locally after a conflict.
        if (err.status === 409) setReload((value) => value + 1);
      }
    } finally { clearTimeout(timeout); setSaving(false); }
  }
  function saveLocal() {
    try { const clean = cleanDraft(); localStorage.setItem(PREFIX + key, JSON.stringify(clean)); setLocal(clean); setEditing(false); setError(''); setMessage('이 브라우저에만 저장했습니다. 서버 자료가 있으면 서버의 직접 입력 자료를 우선 표시합니다.'); }
    catch (err) { setError(err.message); }
  }
  function removeLocal() {
    try { localStorage.removeItem(PREFIX + key); setLocal(null); setMessage('브라우저 저장 정보를 삭제했습니다.'); setError(''); }
    catch { setError('브라우저 저장 정보를 지우지 못했습니다.'); }
  }
  const labels = { manual: '서버 공유 · 직접 입력', public: '공공데이터 자동 조회', local: '이 브라우저에만 저장', catalog: '공개자료 파일 등록' };
  return <section className="place-operating-info" aria-label={`${place.name} 운영정보`}>
    <div className="poi-heading"><strong>운영정보</strong><span className={`poi-status poi-${info?.status || 'unknown'}`}>{STATES[info?.status || 'unknown']}</span></div>
    <dl><dt>운영시간</dt><dd>{info?.hours || '확인 필요'}</dd><dt>휴무일</dt><dd>{info?.closedDays || '확인 필요'}</dd></dl>
    {info && <p className="poi-meta">{labels[info.origin] || '등록 정보'} · 출처: {info.source}<br/>{info.origin === 'public' ? '조회일' : '확인일'} {info.checkedOn}{info.sourceUpdatedOn && <> · 원자료 기준일 {info.sourceUpdatedOn}</>}</p>}
    {info?.origin === 'local' && <p className="poi-meta">기존 브라우저 저장 정보입니다. ‘정보 입력·수정 → 서버에 저장’으로 공유할 수 있습니다.</p>}
    <p className="poi-meta">{info ? '임시휴무·휴게시간이 달라질 수 있습니다. 방문 전 상세정보나 전화로 확인해 주세요.' : '등록된 운영정보가 없습니다. 정보 없음은 폐업을 의미하지 않습니다.'}</p>
    {server?.message && <p className="poi-meta">{server.message}</p>}
    {info?.note && <p className="poi-meta">{info.note}</p>}
    {server?.stale && <p className="poi-meta">자동 조회 실패로 이전 자료를 표시하고 있습니다.</p>}
    {loadError && <p className="poi-meta">{loadError} <button type="button" onClick={() => setReload((value) => value + 1)}>다시 조회</button></p>}
    <div className="poi-actions">
      {place.url && <a href={place.url} target="_blank" rel="noreferrer">카카오맵에서 확인 ↗</a>}
      {place.phone && <a href={`tel:${place.phone.replace(/[^+\d-]/g, '')}`}>전화 {place.phone}</a>}
      <button type="button" disabled={saving} onClick={() => { setDraft({ ...empty(), ...(info || {}), checkedOn: today() }); setToken(editKey); setError(''); setMessage(''); setEditing(!editing); }}>{editing ? '입력 닫기' : '정보 입력·수정'}</button>
    </div>
    {editing && <div className="poi-editor">
      <p>상세페이지·공식 홈페이지에서 확인한 내용을 입력해 주세요. 서버에 저장하면 같은 서버를 사용하는 사용자에게 공유됩니다.</p>
      <label>운영시간<textarea maxLength={2000} rows={3} value={draft.hours} onChange={(e) => setDraft({ ...draft, hours: e.target.value })}/></label>
      <label>휴무일<input maxLength={500} value={draft.closedDays} placeholder="매주 일요일 / 연중무휴 / 확인 필요" onChange={(e) => setDraft({ ...draft, closedDays: e.target.value })}/></label>
      <label>운영 상태<select value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value })}>{Object.entries(STATES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>정보 출처 (필수)<input maxLength={1000} value={draft.source} onChange={(e) => setDraft({ ...draft, source: e.target.value })}/></label>
      <label>확인일 (필수)<input type="date" max={today()} value={draft.checkedOn} onChange={(e) => setDraft({ ...draft, checkedOn: e.target.value })}/></label>
      <label>서버 저장용 편집키<input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="설치 시 생성된 운영정보 편집키"/></label>
      <div className="poi-actions"><button type="button" disabled={saving || !server} onClick={() => changeShared(false)}>{saving ? '저장 중…' : '서버에 저장'}</button><button type="button" disabled={saving} onClick={saveLocal}>이 브라우저에만 저장</button><button type="button" disabled={saving} onClick={() => setEditing(false)}>취소</button>{local && <button type="button" disabled={saving} onClick={removeLocal}>브라우저 정보 삭제</button>}{server?.info?.origin === 'manual' && <button type="button" disabled={saving} onClick={() => changeShared(true)}>서버 직접 입력 삭제</button>}</div>
    </div>}
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
  </section>;
}
