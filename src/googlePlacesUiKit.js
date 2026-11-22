// Google content stays in its UI Kit. Automatic linking uses the address query
// and one unique nearby Google result; only its place ID is remembered.
export const GOOGLE_KEY = (import.meta.env.VITE_GOOGLE_MAPS_API_KEY || '').trim();
const LINK_PREFIX = 'cinespot:google-place-link:v1:';
let sdkPromise = null;
let sdkKey = null;

export function kakaoPlaceId(place) {
  return String(place.id || '').replace(/^(food|spot|parking)-/, '');
}
export function validGoogleId(value) {
  return typeof value === 'string' && /^[A-Za-z0-9_-]{1,512}$/.test(value);
}
export function readLink(key) {
  try {
    const value = JSON.parse(localStorage.getItem(LINK_PREFIX + key));
    return validGoogleId(value?.placeId) ? value.placeId : '';
  } catch { return ''; }
}
export function writeLink(key, placeId) {
  if (!validGoogleId(placeId)) return false;
  try { localStorage.setItem(LINK_PREFIX + key, JSON.stringify({ placeId })); return true; }
  catch { return false; }
}
export function removeLink(key) {
  try { localStorage.removeItem(LINK_PREFIX + key); return true; }
  catch { return false; }
}
export function placeQuery(place) {
  return `${place.name || ''} ${place.address || '부산'}`.trim();
}
export function automaticPlaceId(place, candidates) {
  if (!place.name?.trim() || !place.address?.trim() || !Number.isFinite(place.lat) || !Number.isFinite(place.lng)) return '';
  const nearby = new Map();
  for (const candidate of candidates || []) {
    const point = candidate.location;
    const lat = typeof point?.lat === 'function' ? point.lat() : point?.lat;
    const lng = typeof point?.lng === 'function' ? point.lng() : point?.lng;
    if (!validGoogleId(candidate.id) || !Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) continue;
    const radians = (value) => value * Math.PI / 180;
    const a = Math.sin(radians(lat - place.lat) / 2) ** 2 + Math.cos(radians(lat)) * Math.cos(radians(place.lat)) * Math.sin(radians(lng - place.lng) / 2) ** 2;
    const meters = 6371000 * 2 * Math.asin(Math.min(1, Math.sqrt(a)));
    if (meters <= 80) nearby.set(candidate.id, candidate);
  }
  return nearby.size === 1 ? nearby.keys().next().value : '';
}
export function loadGooglePlaces(key = GOOGLE_KEY) {
  if (!key) return Promise.reject(new Error('Google Maps 키를 설정해 주세요.'));
  if (sdkKey && sdkKey !== key) return Promise.reject(new Error('변경한 Google 키를 적용하려면 페이지를 새로고침해 주세요.'));
  if (sdkPromise) return sdkPromise;
  sdkKey = key;
  sdkPromise = (async () => {
    if (!window.google?.maps?.importLibrary) {
      await new Promise((resolve, reject) => {
        const callback = '__cinespotGooglePlacesReady';
        const script = document.createElement('script');
        script.id = 'cinespot-google-places-sdk';
        script.async = true;
        let finished = false;
        let timeout;
        const finish = (error) => {
          if (finished) return;
          finished = true; clearTimeout(timeout);
          delete window[callback]; script.onerror = null;
          if (error) { script.remove(); reject(error); } else resolve();
        };
        window[callback] = () => finish();
        script.onerror = () => finish(new Error('Google 연결에 실패했습니다. 네트워크를 확인해 주세요.'));
        const params = new URLSearchParams({ key, v: 'weekly', libraries: 'places', language: 'ko', region: 'KR', loading: 'async', callback });
        script.src = `https://maps.googleapis.com/maps/api/js?${params}`;
        timeout = setTimeout(() => finish(new Error('Google 연결 시간이 초과되었습니다.')), 25000);
        document.head.appendChild(script);
      });
    }
    const library = await window.google.maps.importLibrary('places');
    if (!library.PlaceSearchElement || !library.PlaceDetailsElement) {
      throw new Error('Places UI Kit를 불러오지 못했습니다. 키 설정과 API 사용 설정을 확인해 주세요.');
    }
    return library;
  })().catch((error) => { sdkPromise = null; throw error; });
  return sdkPromise;
}

// Use only documented UI Kit elements. No hidden API calls or DOM text scraping.
export function mountGoogleInfo(host, place, library, onState, remember) {
  const key = kakaoPlaceId(place);
  let disposed = false;
  let detach = () => {};
  let timer;
  const update = (state, message = '') => { if (!disposed) onState(state, message); };
  const element = (name) => document.createElement(name);
  function show(widget, load, select) {
    detach(); clearTimeout(timer);
    const loaded = () => { if (!disposed) { clearTimeout(timer); load(); } };
    const failed = () => { clearTimeout(timer); update('error', 'Google 조회에 실패했습니다. 키의 사용 설정·웹사이트 제한·할당량을 확인해 주세요.'); };
    widget.addEventListener('gmp-load', loaded);
    widget.addEventListener('gmp-error', failed);
    if (select) widget.addEventListener('gmp-select', select);
    detach = () => {
      widget.removeEventListener('gmp-load', loaded);
      widget.removeEventListener('gmp-error', failed);
      if (select) widget.removeEventListener('gmp-select', select);
    };
    timer = setTimeout(() => update('error', 'Google 응답을 기다리고 있습니다. 잠시 후 다시 조회해 주세요.'), 30000);
    host.replaceChildren(widget);
  }
  function details(id, newlyMatched = false) {
    if (!validGoogleId(id) || disposed) return;
    update('loading', 'Google 운영정보를 불러오는 중입니다.');
    const widget = new library.PlaceDetailsElement();
    const request = element('gmp-place-details-place-request');
    request.place = id;
    widget.appendChild(request);
    // The running SDK accepts a content-config child; field elements belong inside it.
    const content = element('gmp-place-content-config');
    for (const name of ['gmp-place-address', 'gmp-place-open-now-status', 'gmp-place-opening-hours']) content.appendChild(element(name));
    widget.appendChild(content);
    show(widget, () => {
      let message = 'Google에 등록된 운영정보입니다. 운영시간 항목이 없으면 확인이 필요합니다.';
      if (newlyMatched && !remember(key, id)) message += ' 브라우저에 장소 연결을 저장하지 못했습니다.';
      update('details', message);
    });
  }
  function search() {
    update('loading', '주소와 위치로 운영시간을 자동 조회하는 중입니다.');
    const widget = new library.PlaceSearchElement({ selectable: false });
    widget.selectable = false;
    const content = element('gmp-place-content-config');
    content.appendChild(element('gmp-place-address'));
    widget.appendChild(content);
    const request = element('gmp-place-text-search-request');
    request.maxResultCount = 20;
    if (Number.isFinite(place.lat) && Number.isFinite(place.lng)) request.locationBias = { center: { lat: place.lat, lng: place.lng }, radius: 1500 };
    request.textQuery = placeQuery(place);
    widget.appendChild(request);
    show(widget, () => {
      const match = automaticPlaceId(place, widget.places);
      if (match) { details(match, true); return; }
      // Do not turn an uncertain result into a user selection step or an opening schedule.
      detach(); clearTimeout(timer); host.replaceChildren();
      update('empty', widget.places?.length ? '운영시간 확인 필요: 주소 검색 결과에서 80m 이내 장소를 하나로 좁히지 못했습니다.' : '운영시간 확인 필요: Google에 해당 장소의 검색 결과가 없습니다.');
    });
  }
  const saved = readLink(key);
  if (saved) details(saved); else search();
  return () => { disposed = true; detach(); clearTimeout(timer); host.replaceChildren(); };
}
