import { useEffect, useMemo, useRef, useState } from 'react';

// 영화 공공데이터는 전체 촬영 이력입니다. 방문 추천은 검증한 대표 명소만 별도로 선정합니다.
// 선정 기준: 부산시/부산 관광 공식 소개에서 영화 연관성과 대표 방문 장소 확인.
// 다른 작품을 추가할 때도 외부 근거를 확인한 뒤 목록에 등록하세요.
const FEATURED_FILM_LOCATIONS = {
  '국제시장': [
    {
      id: 'featured-gukje-flower',
      name: '꽃분이네',
      kakaoKeyword: '부산 국제시장 꽃분이네',
      note: '국제시장 안에 있는 영화 속 덕수 가족의 가게로 알려진 대표 명소',
      filmRelation: '실제 영화 촬영 명소',
      sourceUrl: 'https://www.busan.go.kr/news/specnews/view?dataNo=46532&gugun=Prev',
    },
  ],
  '변호인': [
    {
      id: 'featured-attorney-coast',
      name: '절영해안산책로',
      kakaoKeyword: '부산 절영해안산책로',
      note: '영화 촬영지 흰여울문화마을과 연결되는 대표 해안 산책 코스',
      filmRelation: '흰여울문화마을과 이어지는 해안 산책 코스',
      sourceUrl: 'https://www.busan.go.kr/news/totalnews01/view?dataNo=48203&gugun=Prev',
    },
  ],
};

function featuredLocationsFor(movie) {
  const normalized = String(movie || '').replace(/\s+/g, '');
  const candidates = FEATURED_FILM_LOCATIONS[normalized] || [];
  return candidates.map((location) => ({ ...location, movie: normalized }));
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const RADIUS_METERS = 2000; // SRS FR-004: 촬영지 반경 1~2km
const TYPES = [
  { id: 'food', label: '근처 맛집', short: '맛집' },
  { id: 'spot', label: '갈 만한 장소', short: '관광' },
  { id: 'parking', label: '주변 주차장', short: '주차' },
];
let kakaoSdkPromise;

function loadKakaoSdk(key) {
  if (window.kakao?.maps?.services && window.kakao.maps.load) {
    return new Promise((resolve) => window.kakao.maps.load(resolve));
  }
  if (!kakaoSdkPromise) {
    kakaoSdkPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(key)}&autoload=false&libraries=services`;
      script.async = true;
      script.onload = () => {
        if (!window.kakao?.maps?.load) {
          kakaoSdkPromise = undefined;
          reject(new Error('카카오맵 SDK 로드 실패: 키와 도메인을 확인해 주세요.'));
          return;
        }
        window.kakao.maps.load(() => {
          if (window.kakao.maps.services) resolve();
          else {
            kakaoSdkPromise = undefined;
            reject(new Error('카카오맵 services 라이브러리를 불러오지 못했습니다.'));
          }
        });
      };
      script.onerror = () => {
        kakaoSdkPromise = undefined;
        reject(new Error('카카오맵 SDK 요청이 실패했습니다.'));
      };
      document.head.appendChild(script);
    });
  }
  return kakaoSdkPromise;
}

function haversine(a, b) {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLng = (b.lng - a.lng) * rad;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * 6371000 * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
}

function formatMeters(meters) {
  return meters >= 1000 ? `${(meters / 1000).toFixed(1)}km` : `${Math.round(meters)}m`;
}

function toKakaoPlace(raw, kind, anchor) {
  const lat = Number(raw.y);
  const lng = Number(raw.x);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  const place = {
    id: `${kind}-${raw.id}`,
    name: raw.place_name,
    address: raw.road_address_name || raw.address_name || '',
    lat,
    lng,
    kind,
    url: raw.place_url || '',
    phone: raw.phone || '',
  };
  return { ...place, distance: Math.round(haversine(anchor, place)) };
}

function categorySearch(kakao, category, anchor) {
  return new Promise((resolve, reject) => {
    const search = new kakao.maps.services.Places();
    search.categorySearch(category, (items, status) => {
      if (status === kakao.maps.services.Status.OK) resolve(items || []);
      else if (status === kakao.maps.services.Status.ZERO_RESULT) resolve([]);
      else reject(new Error(`카카오 장소 검색이 실패했습니다. (${category}: ${status})`));
    }, {
      location: new kakao.maps.LatLng(anchor.lat, anchor.lng),
      radius: RADIUS_METERS,
      sort: kakao.maps.services.SortBy.DISTANCE,
      page: 1,
    });
  });
}

async function loadNearby(kakao, anchor) {
  const [restaurants, attractions, culture, parking] = await Promise.all([
    categorySearch(kakao, 'FD6', anchor),
    categorySearch(kakao, 'AT4', anchor),
    categorySearch(kakao, 'CT1', anchor),
    categorySearch(kakao, 'PK6', anchor),
  ]);
  const normalize = (rows, kind, count) => {
    const unique = new Map();
    for (const row of rows) {
      const place = toKakaoPlace(row, kind, anchor);
      if (place && place.distance <= RADIUS_METERS && !unique.has(row.id)) unique.set(row.id, place);
    }
    return [...unique.values()].sort((a, b) => a.distance - b.distance).slice(0, count);
  };
  return {
    food: normalize(restaurants, 'food', 10),
    spot: normalize([...attractions, ...culture], 'spot', 10),
    parking: normalize(parking, 'parking', 5),
  };
}

function searchKakaoPlaceByKeyword(kakao, keyword) {
  return new Promise((resolve) => {
    const places = new kakao.maps.services.Places();
    places.keywordSearch(keyword, (items, status) => {
      resolve(status === kakao.maps.services.Status.OK ? (items || []) : []);
    });
  });
}

async function resolveFilmingPosition(kakao, place) {
  const lat = Number(place.lat);
  const lng = Number(place.lng);
  if (place.lat != null && place.lng != null && Number.isFinite(lat) && Number.isFinite(lng)
      && lat > 34 && lat < 36 && lng > 128 && lng < 130) {
    return { lat, lng, estimated: false };
  }
  if (place.address) {
    return new Promise((resolve) => {
      new kakao.maps.services.Geocoder().addressSearch(place.address, (items, status) => {
        resolve(status === kakao.maps.services.Status.OK && items.length
          ? { lat: Number(items[0].y), lng: Number(items[0].x), estimated: false }
          : null);
      });
    });
  }

  // 요구된 UX: 카카오 장소명 검색 결과 중 첫 번째 위치를 사용합니다.
  // 이 좌표는 API가 인증한 실제 촬영 지점이 아니므로 UI에서 '추정 위치'로 표시합니다.
  const cleaned = String(place.name || '')
    .replace(/\[[^\]]*\]/g, '')
    .replace(/\([^)]*\)/g, '')
    .trim();
  if (!cleaned) return null;
  for (const query of [place.kakaoKeyword, `부산 ${cleaned}`, cleaned].filter(Boolean)) {
    const items = await searchKakaoPlaceByKeyword(kakao, query);
    const first = items[0];
    if (!first) continue;
    const resultLat = Number(first.y);
    const resultLng = Number(first.x);
    if (!Number.isFinite(resultLat) || !Number.isFinite(resultLng)) continue;
    return {
      lat: resultLat,
      lng: resultLng,
      estimated: true,
      resolvedName: first.place_name || '',
      resolvedAddress: first.road_address_name || first.address_name || '',
    };
  }
  return null;
}

// 출발 영화 촬영지를 고정하고, 다음 지점부터 직선거리 기반 최근접 + 2-opt로 정렬합니다.
// 도로/교통/실제 이동시간을 반영하는 길찾기 알고리즘이 아닙니다.
function suggestOrder(start, stops) {
  const remaining = [...stops];
  const ordered = [];
  let current = start;
  while (remaining.length) {
    let minIndex = 0;
    for (let i = 1; i < remaining.length; i += 1) {
      if (haversine(current, remaining[i]) < haversine(current, remaining[minIndex])) minIndex = i;
    }
    current = remaining.splice(minIndex, 1)[0];
    ordered.push(current);
  }
  const distance = (points) => points.reduce((sum, point, index) =>
    sum + haversine(index === 0 ? start : points[index - 1], point), 0);
  let improved = true;
  let passes = 0;
  while (improved && passes < 20) {
    improved = false;
    passes += 1;
    for (let i = 0; i < ordered.length - 1; i += 1) {
      for (let j = i + 1; j < ordered.length; j += 1) {
        const next = [...ordered];
        next.splice(i, j - i + 1, ...ordered.slice(i, j + 1).reverse());
        if (distance(next) + 0.1 < distance(ordered)) {
          ordered.splice(0, ordered.length, ...next);
          improved = true;
        }
      }
    }
  }
  return ordered;
}

function kakaoDestinationUrl(place) {
  return `https://map.kakao.com/link/to/${encodeURIComponent(place.name)},${place.lat},${place.lng}`;
}

// Kakao 지도는 길찾기 링크에서 최대 5개 경유지를 지원합니다.
function kakaoCourseUrl(route) {
  if (route.length < 2 || route.length > 7) return '';
  return `https://map.kakao.com/link/by/walk/${route.map((p) => `${encodeURIComponent(p.name)},${p.lat},${p.lng}`).join('/')}`;
}

function getStopLabel(place) {
  if (place.kind === 'parking') return '주차';
  if (place.kind === 'film') return '촬영지';
  return place.kind === 'food' ? '맛집' : '관광';
}


const STORAGE_KEY = 'cinespot-busan-saved-courses-v1';

function readSavedCourses() {
  try {
    const value = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '[]');
    return Array.isArray(value) ? value.filter((record) => Array.isArray(record.route)) : [];
  } catch {
    return [];
  }
}

function SingleLocationMap({ place }) {
  const nodeRef = useRef(null);
  const mapRef = useRef(null);
  const markerRef = useRef(null);

  useEffect(() => {
    if (!nodeRef.current || !place || !window.kakao?.maps) return;
    const { kakao } = window;
    const map = new kakao.maps.Map(nodeRef.current, {
      center: new kakao.maps.LatLng(place.lat, place.lng), level: 4,
    });
    mapRef.current = map;
    window.requestAnimationFrame(() => map.relayout());
    return () => {
      markerRef.current?.setMap(null);
      markerRef.current = null;
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !place) return;
    const { kakao } = window;
    // 항상 지도 1개 + 현재 선택 위치 마커 1개만 유지합니다.
    markerRef.current?.setMap(null);
    const point = new kakao.maps.LatLng(place.lat, place.lng);
    map.setCenter(point);
    map.setLevel(4);
    markerRef.current = new kakao.maps.Marker({ map, position: point, title: place.name });
    window.requestAnimationFrame(() => map.relayout());
    return () => markerRef.current?.setMap(null);
  }, [place?.id, place?.lat, place?.lng]);

  return <div className="map-canvas" ref={nodeRef} aria-label="현재 선택한 장소의 카카오 지도" />;
}

function CourseMap({ route, ready }) {
  const nodeRef = useRef(null);
  useEffect(() => {
    if (!ready || !nodeRef.current || !route.length || !window.kakao?.maps) return;
    const { kakao } = window;
    const map = new kakao.maps.Map(nodeRef.current, {
      center: new kakao.maps.LatLng(route[0].lat, route[0].lng), level: 5,
    });
    const bounds = new kakao.maps.LatLngBounds();
    const markers = [];
    const overlays = [];
    route.forEach((stop, index) => {
      const point = new kakao.maps.LatLng(stop.lat, stop.lng);
      bounds.extend(point);
      const marker = new kakao.maps.Marker({ map, position: point, title: `${index + 1}. ${stop.name}` });
      markers.push(marker);
      const dot = document.createElement('span');
      dot.className = 'route-pin';
      dot.textContent = String(index + 1);
      const overlay = new kakao.maps.CustomOverlay({ map, position: point, content: dot, yAnchor: 1.65 });
      overlays.push(overlay);
      kakao.maps.event.addListener(marker, 'click', () => {
        const link = kakaoDestinationUrl(stop);
        window.open(link, '_blank', 'noopener,noreferrer');
      });
    });
    const line = route.length > 1 ? new kakao.maps.Polyline({
      map,
      path: route.map((stop) => new kakao.maps.LatLng(stop.lat, stop.lng)),
      strokeWeight: 4,
      strokeColor: '#3479f6',
      strokeOpacity: 0.85,
      strokeStyle: 'shortdash',
    }) : null;
    if (route.length > 1) map.setBounds(bounds, 70, 45, 60, 45);
    else map.setLevel(4);
    window.requestAnimationFrame(() => map.relayout());
    return () => {
      markers.forEach((marker) => marker.setMap(null));
      overlays.forEach((overlay) => overlay.setMap(null));
      line?.setMap(null);
    };
  }, [ready, route]);

  return <div className="map-canvas" ref={nodeRef} aria-label="선택한 코스만 표시한 카카오 지도" />;
}

function useKakaoReady() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const key = import.meta.env.VITE_KAKAO_JAVASCRIPT_KEY;
    if (!key) {
      setError('프로젝트 루트 .env의 VITE_KAKAO_JAVASCRIPT_KEY가 필요합니다.');
      return;
    }
    let cancelled = false;
    loadKakaoSdk(key)
      .then(() => { if (!cancelled) setReady(true); })
      .catch((err) => { if (!cancelled) setError(err.message); });
    return () => { cancelled = true; };
  }, []);
  return { ready, error };
}

function RouteDisplay({ route, ready }) {
  return (
    <>
      <div className="planner-layout route-layout">
        <div className="planner-card">
          <div className="section-head"><h3>추천 방문 순서</h3><span>{route.length}개 장소</span></div>
          <ol className="route-stops">
            {route.map((stop, i) => (
              <li key={`${stop.id}-${i}`}>
                <span className="stop-num">{i + 1}</span>
                <div><b>{stop.name}</b><small>{getStopLabel(stop)}{i === 0 ? ' · 출발' : i === route.length - 1 ? ' · 마지막 장소' : ''}</small></div>
                <a href={kakaoDestinationUrl(stop)} target="_blank" rel="noreferrer">길찾기 ↗</a>
              </li>
            ))}
          </ol>
          {kakaoCourseUrl(route) && (
            <a className="route-external" href={kakaoCourseUrl(route)} target="_blank" rel="noreferrer">카카오맵에서 길찾기 ↗</a>
          )}
        </div>
        <section className="map-panel" aria-label="추천 코스 지도">
          <div className="map-panel-header"><strong>코스 지도</strong><span>{route.length}개 장소</span></div>
          {ready ? <CourseMap route={route} ready={ready}/> : <div className="map-unavailable">카카오맵을 불러오는 중...</div>}
        </section>
      </div>
    </>
  );
}

function FilmExplorer({ movie, locations, onReset, onSave, onRecommend }) {
  const featuredLocations = useMemo(() => featuredLocationsFor(movie), [movie]);
  const { ready, error: sdkError } = useKakaoReady();
  const [positions, setPositions] = useState([]);
  const [resolving, setResolving] = useState(true);
  const [filmIndex, setFilmIndex] = useState(null);
  const [mode, setMode] = useState('explore'); // explore -> choose -> route
  const [tab, setTab] = useState('food');
  const [nearby, setNearby] = useState({ food: [], spot: [], parking: [] });
  const [nearbyState, setNearbyState] = useState('idle');
  const [nearbyError, setNearbyError] = useState('');
  const [checked, setChecked] = useState([]);
  const [parkingId, setParkingId] = useState('');
  const [saveMessage, setSaveMessage] = useState('');
  const [currentRecommendation, setCurrentRecommendation] = useState(null);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    setResolving(true);
    (async () => {
      // 전체 공공데이터를 일괄 지도 표시하지 않고, 선정된 대표 명소만 좌표 검색합니다.
      const resolved = new Array(featuredLocations.length).fill(null);
      for (let i = 0; i < featuredLocations.length; i += 3) {
        const batch = await Promise.all(featuredLocations.slice(i, i + 3)
          .map((place) => resolveFilmingPosition(window.kakao, place)));
        if (cancelled) return;
        batch.forEach((position, idx) => { resolved[i + idx] = position; });
        setPositions([...resolved]); // 순차 결과를 즉시 화면에 반영
      }
      if (!cancelled) setResolving(false);
    })().catch((err) => { if (!cancelled) { setResolving(false); setNearbyError(err.message); } });
    return () => { cancelled = true; };
  }, [ready, featuredLocations]);

  const anchor = filmIndex === null ? null : positions[filmIndex] || null;
  useEffect(() => {
    if (!ready || !anchor) return;
    let cancelled = false;
    setNearbyState('loading');
    setNearbyError('');
    setNearby({ food: [], spot: [], parking: [] });
    setChecked([]);
    setParkingId('');
    loadNearby(window.kakao, anchor).then((data) => {
      if (cancelled) return;
      setNearby(data);
      setParkingId(data.parking[0]?.id || '');
      setNearbyState('ready');
    }).catch((err) => {
      if (!cancelled) { setNearbyState('error'); setNearbyError(err.message); }
    });
    return () => { cancelled = true; };
  }, [ready, filmIndex, anchor?.lat, anchor?.lng]);

  const film = anchor && filmIndex !== null
    ? { ...anchor, id: `film-${filmIndex}`, name: featuredLocations[filmIndex].name, kind: 'film' }
    : null;
  const selectedPlaces = useMemo(
    () => [...nearby.food, ...nearby.spot].filter((p) => checked.includes(p.id)),
    [nearby, checked]
  );
  const parking = nearby.parking.find((p) => p.id === parkingId) || null;
  const route = useMemo(() => {
    if (!film) return [];
    return [...(parking ? [parking] : []), film, ...suggestOrder(film, selectedPlaces)];
  }, [film?.id, film?.lat, film?.lng, selectedPlaces, parking]);

  function selectFilm(index) {
    const next = index === filmIndex ? null : index;
    setFilmIndex(next);
    setChecked([]);
    setParkingId('');
    setTab('food');
  }
  function moveMode(next) {
    setMode(next);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  function createCourseRecord() {
    return {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      title: `${movie} · ${film?.name || '영화 촬영지'} 코스`,
      movie,
      createdAt: new Date().toISOString(),
      route: route.map(({ id, name, lat, lng, kind, address, estimated }) => ({
        id, name, lat, lng, kind, address: address || '', estimated: Boolean(estimated),
      })),
    };
  }
  function finalizeRecommendation() {
    const record = createCourseRecord();
    setCurrentRecommendation(record);
    onRecommend(record); // 사용자 저장과 별개로, 생성된 추천 코스를 자동 기록
    setSaveMessage('');
    moveMode('route');
  }
  function saveCourse() {
    const ok = onSave(currentRecommendation || createCourseRecord());
    setSaveMessage(ok
      ? '코스가 저장되었습니다.'
      : '저장에 실패했어. 브라우저 저장 설정을 확인해 줘.');
  }
  function downloadCourse() {
    const record = currentRecommendation || createCourseRecord();
    const blob = new Blob([JSON.stringify(record, null, 2)], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const element = document.createElement('a');
    element.href = url;
    element.download = `CineSpot_${movie.replace(/[\\/:*?"<>|]/g, '_')}_course.json`;
    document.body.appendChild(element);
    element.click();
    element.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  if (sdkError) return <p className="notice" role="alert">{sdkError}</p>;
  return <section className="results journey-results" aria-label="부산 영화 촬영지 및 코스 추천">
    <div className="results-title">
      <div><span className="eyebrow">CINESPOT · BUSAN</span>
        <h2>{mode === 'explore' ? `“${movie}” 영화 촬영지` : mode === 'choose' ? '주변 탐방 장소 선택' : '나의 부산 영화 코스'}</h2>
        {mode !== 'route' && (
          <p>{mode === 'explore' ? '방문할 영화 명소를 선택하세요.' : '코스에 추가할 장소를 선택하세요.'}</p>
        )}
      </div>
      {mode === 'explore' && <span className="result-count">대표 명소 {featuredLocations.length}곳</span>}
    </div>
    <div className="stepbar" aria-label="진행 단계">
      <span className={mode === 'explore' ? 'current' : ''}>1 촬영지 선택</span>
      <span className={mode === 'choose' ? 'current' : ''}>2 주변 탐방</span>
      <span className={mode === 'route' ? 'current' : ''}>3 추천 코스</span>
    </div>

    {mode === 'explore' && <>
      <div className="planner-card film-pick-screen">
        <div className="section-head"><h3>대표 영화 명소</h3><span>엄선 {featuredLocations.length}곳</span></div>

        {!featuredLocations.length && <div className="featured-empty">
          <strong>이 영화의 대표 명소는 아직 선정되지 않았어.</strong>
          <p>공공데이터에는 촬영 장소 {locations.length}곳이 있지만, 관광 명소로 검증되기 전에는 임의로 추천하지 않아. 새 영화로 검색해 줘.</p>
        </div>}
        <div className="film-list">
          {featuredLocations.map((place, i) => (
            <div className="film-entry" key={`${place.id}-${i}`}>
              <button type="button" className={`film-choice ${filmIndex === i ? 'active' : ''}`}
                aria-expanded={filmIndex === i} aria-controls={`inline-map-${i}`}
                onClick={() => selectFilm(i)}>
                <span className="film-no">{String(i + 1).padStart(2, '0')}</span>
                <span className="film-name"><strong>{place.name}</strong>
                  <small>{place.filmRelation}</small>
                  <small>{positions[i] ? '장소 위치 보기' : resolving ? '위치 확인 중...' : '위치 정보 없음'}</small>
                </span>
                <span className="choice-chevron">{filmIndex === i ? '접기 −' : '지도 보기 ＋'}</span>
              </button>
              {filmIndex === i && <div id={`inline-map-${i}`} className="inline-map-wrap">
                {ready && positions[i] ? <>
                  <SingleLocationMap key={`film-map-${i}`} place={{ ...positions[i], name: place.name, id: place.id }}/>
                  <div className="map-selection-note"><strong>{place.name}</strong>
                    <span>{place.note}</span>
                    {positions[i].estimated && <span>카카오맵 장소 검색 위치</span>}
                    <div className="featured-links"><a href={place.sourceUrl} target="_blank" rel="noreferrer">선정 근거 보기 ↗</a>
                    <a href={kakaoDestinationUrl({ ...positions[i], name: place.name })} target="_blank" rel="noreferrer">카카오맵에서 크게 보기 ↗</a></div>
                  </div>
                </> : <p className="inline-map-pending">{resolving ? '카카오맵에서 이 촬영지의 위치를 찾고 있어...' : '해당 장소의 위치를 검색하지 못했어. 다른 촬영지를 선택해 줘.'}</p>}
              </div>}
            </div>
          ))}
        </div>
        {resolving && <p className="minor-note">촬영지 위치 검색 중 · 확인된 {positions.filter(Boolean).length}곳</p>}
        {!resolving && <p className="source-line">정보 출처: 부산광역시·부산영상위원회, 부산 관광 자료, 카카오맵</p>}
      </div>
      <div className="bottom-actions">
        <p>{film ? `선택한 촬영지: ${film.name}` : '먼저 방문할 영화 촬영지를 선택해 줘.'}</p>
        <button type="button" className="route-button" disabled={!film} onClick={() => moveMode('choose')}>코스 추천받기 →</button>
        <button type="button" className="secondary-button" onClick={onReset}>재검색하기</button>
      </div>
    </>}

    {mode === 'choose' && <div className="planner-card selection-screen">
      <div className="section-head"><h3>근처 맛집 · 갈 만한 장소 · 주차장</h3><span>선택한 촬영지 반경 2km</span></div>
      <div className="selected-film-summary"><span>선택한 영화 촬영지</span><strong>{film?.name}</strong>
</div>
      <div className="place-tabs" role="tablist" aria-label="방문 장소 유형">
        {TYPES.map((type) => <button key={type.id} type="button" role="tab" aria-selected={tab === type.id}
          className={`place-tab ${tab === type.id ? 'active' : ''}`} onClick={() => setTab(type.id)}>{type.label} <small>{nearby[type.id].length}</small></button>)}
      </div>
      {nearbyState === 'loading' && <p className="places-status">카카오맵에서 주변 장소를 불러오는 중...</p>}
      {nearbyState === 'error' && <p className="warning-note">{nearbyError}</p>}
      {tab === 'parking' && <label className="nearby-item no-parking"><input type="radio" name="course-parking" checked={!parkingId} onChange={() => setParkingId('')}/><span>주차장 선택 안 함</span></label>}
      <div className="nearby-list selectable-list">
        {nearby[tab].map((place, i) => <div className={`nearby-item ${checked.includes(place.id) || parkingId === place.id ? 'picked' : ''}`} key={place.id}>
          <label className="nearby-option">
            <input type={tab === 'parking' ? 'radio' : 'checkbox'} name={tab === 'parking' ? 'course-parking' : undefined}
              checked={tab === 'parking' ? parkingId === place.id : checked.includes(place.id)}
              onChange={() => tab === 'parking' ? setParkingId(place.id) : setChecked((old) => old.includes(place.id) ? old.filter((id) => id !== place.id) : [...old, place.id])}/>
            <span className="nearby-copy"><strong>{i + 1}. {place.name}</strong><small>촬영지에서 직선거리 {formatMeters(place.distance)}</small><small>{place.address || '주소 없음'}</small></span>
          </label>
          {place.url && <a className="details-link" href={place.url} target="_blank" rel="noreferrer">상세 ↗</a>}
        </div>)}
      </div>
      {nearbyState === 'ready' && !nearby[tab].length && <p className="places-status">이 유형의 검색 결과가 없어.</p>}
      <p className="source-line">장소 정보 제공: 카카오맵</p>
      <div className="choose-summary">선택한 맛집·관광지 <strong>{checked.length}곳</strong><span> · 주차장 {parking ? '1곳 선택' : '미선택'}</span></div>
      <div className="action-row">
        <button type="button" className="secondary-button" onClick={() => moveMode('explore')}>← 촬영지 다시 선택</button>
        <button type="button" className="route-button" disabled={!selectedPlaces.length || nearbyState !== 'ready'} onClick={finalizeRecommendation}>선택한 장소로 코스 만들기 →</button>
      </div>
    </div>}

    {mode === 'route' && <>
      <RouteDisplay route={route} ready={ready}/>
      <div className="bottom-actions">
        <div className="action-row">
          <button type="button" className="route-button" onClick={saveCourse}>코스 저장하기</button>
          <button type="button" className="secondary-button" onClick={downloadCourse}>코스 파일 내려받기</button>
          <button type="button" className="secondary-button" onClick={() => moveMode('choose')}>장소 다시 고르기</button>
          <button type="button" className="secondary-button" onClick={onReset}>재검색하기</button>
        </div>
        {saveMessage && <p className="save-notice" role="status">{saveMessage}</p>}
      </div>
    </>}
  </section>;
}

function SavedCourses({ courses, onOpen, onDelete, onReset, variant = 'saved' }) {
  const isRecent = variant === 'recent';
  return <section className="saved-list-screen"><div className="results-title"><div><span className="eyebrow">MY CINESPOT</span><h2>{isRecent ? '이전 추천 코스' : '저장한 코스'}</h2><p>{isRecent ? '코스를 생성하면 별도 저장 버튼 없이 자동으로 남아.' : '사용자가 저장한 코스만 보관되는 목록이야.'}</p></div></div>
    {!courses.length && <div className="empty-panel"><h2>{isRecent ? '이전에 추천받은 코스가 없어.' : '저장된 코스가 없어.'}</h2><p>{isRecent ? '영화를 검색해 방문 장소를 선택한 다음 코스를 만들어 줘.' : '영화를 검색하고 코스를 만든 다음 코스 저장하기를 눌러줘.'}</p></div>}
    <div className="saved-list">{courses.map((course) => <div className="saved-card" key={course.id}>
      <div><strong>{course.title}</strong><small>{new Date(course.createdAt).toLocaleString('ko-KR')} · {course.route.length}개 지점</small></div>
      <div className="action-row"><button type="button" className="route-button" onClick={() => onOpen(course)}>코스 열기</button>{!isRecent && <button type="button" className="secondary-button" onClick={() => onDelete(course.id)}>삭제</button>}</div>
    </div>)}</div>
    <button className="secondary-button" type="button" onClick={onReset}>새 영화 검색하기</button>
  </section>;
}

function SavedCourseView({ course, onList, onReset, heading = '저장한 코스' }) {
  const { ready, error } = useKakaoReady();
  return <section className="results">
    <div className="results-title"><div><span className="eyebrow">{heading}</span><h2>{course.title}</h2><p>생성일 {new Date(course.createdAt).toLocaleString('ko-KR')}</p></div></div>
    {error ? <p role="alert" className="notice">{error}</p> : <RouteDisplay route={course.route} ready={ready}/>}
    <div className="bottom-actions"><div className="action-row"><button type="button" className="secondary-button" onClick={onList}>← 코스 목록</button><button type="button" className="route-button" onClick={onReset}>재검색하기</button></div></div>
  </section>;
}

const RECOMMENDATIONS_KEY = 'cinespot-busan-recommended-courses-v1';
function readRecommendedCourses() {
  try {
    const entries = JSON.parse(window.localStorage.getItem(RECOMMENDATIONS_KEY) || '[]');
    return Array.isArray(entries) ? entries.filter((item) => item && Array.isArray(item.route) && item.route.length) : [];
  } catch { return []; }
}

export default function App() {
  const [query, setQuery] = useState('');
  const [locations, setLocations] = useState(null);
  const [movie, setMovie] = useState('');
  const [searchStatus, setSearchStatus] = useState('idle');
  const [notice, setNotice] = useState('');
  const [darkMode, setDarkMode] = useState(false);
  const [actionMenuOpen, setActionMenuOpen] = useState(false);
  const [image, setImage] = useState(null);
  const [view, setView] = useState('search'); // search | results | saved | saved-detail | recent | recent-detail
  const [savedCourses, setSavedCourses] = useState(readSavedCourses);
  const [recommendedCourses, setRecommendedCourses] = useState(readRecommendedCourses);
  const [openedCourse, setOpenedCourse] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const actionMenuRef = useRef(null);
  const fileRef = useRef(null);
  const abortRef = useRef(null);

  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    if (!actionMenuOpen) return;
    const closeOutside = (event) => { if (!actionMenuRef.current?.contains(event.target)) setActionMenuOpen(false); };
    const closeEscape = (event) => { if (event.key === 'Escape') setActionMenuOpen(false); };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeEscape);
    return () => { document.removeEventListener('pointerdown', closeOutside); document.removeEventListener('keydown', closeEscape); };
  }, [actionMenuOpen]);

  async function searchMovie(titleInput) {
    const title = titleInput.trim();
    if (!title) { setNotice('검색할 영화 제목을 입력해 줘.'); return; }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setActionMenuOpen(false);
    setSidebarOpen(false);
    setSearchStatus('loading');
    setNotice('');
    setQuery(title);
    setMovie(title);
    setLocations(null);
    setView('search');
    window.scrollTo({ top: 0, behavior: 'smooth' });
    try {
      const response = await fetch(`${API_BASE}/api/filming-locations?movie=${encodeURIComponent(title)}`, { signal: controller.signal });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `백엔드 오류 (${response.status})`);
      if (!Array.isArray(data.locations)) throw new Error('백엔드에서 locations 배열을 받지 못했어.');
      setLocations(data.locations);
      setSearchStatus(data.locations.length ? 'success' : 'empty');
      setView('results'); // 검색 성공 시 검색창은 화면에서 사라짐
      // 영화 제목만 검색한 행위는 '이전 추천 코스'에 기록하지 않습니다.
      // 장소를 고른 뒤 코스가 실제 생성되었을 때만 기록합니다.
    } catch (err) {
      if (err.name === 'AbortError') return;
      setSearchStatus('error');
      setView('search');
      setNotice(err instanceof TypeError
        ? '백엔드(127.0.0.1:8000)에 연결할 수 없어. FastAPI가 실행 중인지 확인해 줘.'
        : err.message);
    }
  }
  function handleSearch(event) { event.preventDefault(); searchMovie(query); }
  function reset() {
    abortRef.current?.abort();
    setView('search');
    setSearchStatus('idle');
    setQuery('');
    setMovie('');
    setLocations(null);
    setNotice('');
    setImage(null);
    setOpenedCourse(null);
    setActionMenuOpen(false);
    setSidebarOpen(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  function storeCourse(record) {
    try {
      const updated = [record, ...savedCourses].slice(0, 30);
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
      setSavedCourses(updated);
      return true;
    } catch { return false; }
  }
  function deleteCourse(id) {
    const updated = savedCourses.filter((item) => item.id !== id);
    try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(updated)); setSavedCourses(updated); }
    catch { setNotice('저장된 코스를 삭제하지 못했어.'); }
  }
  function showSavedList() {
    abortRef.current?.abort();
    setActionMenuOpen(false);
    setSidebarOpen(false);
    setView('saved');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  function storeRecommendedCourse(record) {
    // 같은 영화/출발점/방문 장소로 만든 코스는 가장 최근 추천 1개로 묶습니다.
    const signature = (item) => `${item.movie}|${item.route.map((stop) => stop.id).join('|')}`;
    const updated = [record, ...recommendedCourses.filter((item) => signature(item) !== signature(record))].slice(0, 30);
    setRecommendedCourses(updated);
    try { window.localStorage.setItem(RECOMMENDATIONS_KEY, JSON.stringify(updated)); } catch { /* 저장 차단 시 세션에서만 유지 */ }
  }
  function showRecommendedList() {
    abortRef.current?.abort();
    setActionMenuOpen(false);
    setSidebarOpen(false);
    setView('recent');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  function clearRecommendations() {
    try { window.localStorage.removeItem(RECOMMENDATIONS_KEY); } catch { /* 무시 */ }
    setRecommendedCourses([]);
  }
  function openRecentCourse(record) {
    setOpenedCourse(record);
    setView('recent-detail');
    setSidebarOpen(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  return <div className={`app-shell ${darkMode ? 'dark' : ''}`}>
    {sidebarOpen && <button type="button" className="sidebar-scrim" aria-label="사이드바 닫기" onClick={() => setSidebarOpen(false)}/>}
    <aside className={`app-sidebar ${sidebarOpen ? 'sidebar-open' : ''}`} aria-label="추천 코스 사이드바">
      <div className="sidebar-header"><button className="sidebar-brand" type="button" onClick={reset}>CineSpot<span>부산</span></button>
        <button className="sidebar-close" type="button" aria-label="사이드바 닫기" onClick={() => setSidebarOpen(false)}>×</button></div>
      <button type="button" className="new-chat-button" onClick={reset}><span aria-hidden="true">＋</span> 새 채팅</button>
      <button type="button" className={`sidebar-shortcut ${view === 'saved' || view === 'saved-detail' ? 'selected' : ''}`} onClick={showSavedList}>
        <span aria-hidden="true">▤</span> 저장한 코스 <small>{savedCourses.length}</small>
      </button>
      <div className="sidebar-history-heading"><button type="button" className="history-heading-button" onClick={showRecommendedList}>이전 추천 코스</button>
        {!!recommendedCourses.length && <button type="button" onClick={clearRecommendations}>모두 지우기</button>}</div>
      <nav className="history-list" aria-label="이전 추천 코스 목록">
        {!recommendedCourses.length && <p className="history-empty">코스를 만들면 여기에 자동으로 표시돼.</p>}
        {recommendedCourses.map((entry) => <button key={entry.id} type="button" title={`${entry.title} 다시 보기`}
          className={`history-item ${view === 'recent-detail' && openedCourse?.id === entry.id ? 'selected' : ''}`}
          onClick={() => openRecentCourse(entry)}><span aria-hidden="true">◷</span><span>{entry.title}</span></button>)}
      </nav>
      <div className="sidebar-bottom">영화 촬영지 · 주변 탐방 · 나만의 코스</div>
    </aside>
    <div className="app-main">
      <header className="topbar">
        <div className="topbar-left"><button type="button" className="sidebar-toggle" aria-label="메뉴 열기" onClick={() => setSidebarOpen(true)}>☰</button>
          <button className="wordmark" type="button" onClick={reset}>CineSpot<span>부산</span></button></div>
        <div className="topbar-actions"><button className="theme-button" type="button" onClick={() => setDarkMode((v) => !v)}>{darkMode ? '☀ 라이트' : '☾ 다크'}</button></div>
      </header>
      <main className="page-content">
        {view === 'search' && <>
          <section className="hero" aria-label="영화 검색">
            <h1>CineSpot부산</h1>
            <p className="hero-subtitle">어떤 부산 영화가 궁금하신가요?</p>
            <form className="searchbar" onSubmit={handleSearch}>
              <div className="add-menu-container" ref={actionMenuRef}>
                <button type="button" className={`search-icon-button add-button ${actionMenuOpen ? 'is-open' : ''}`}
                  aria-label="추가 메뉴" aria-expanded={actionMenuOpen} onClick={() => setActionMenuOpen((v) => !v)}>＋</button>
                {actionMenuOpen && <div className="add-menu">
                  <button type="button" className="add-menu-item" onClick={() => { setActionMenuOpen(false); fileRef.current?.click(); }}>▧ 이미지 추가</button>
                  <button type="button" className="add-menu-item" onClick={() => { setActionMenuOpen(false); setNotice('웹 검색 기능은 아직 연결되지 않았어. 영화 제목 검색 후 코스 추천에서 주변 장소를 확인할 수 있어.'); }}>◎ 웹 검색</button>
                </div>}
              </div>
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="영화 제목을 입력하세요 (예: 국제시장)" aria-label="영화 제목" />
              <button type="submit" className="submit-button" disabled={searchStatus === 'loading'} aria-label="검색하기">{searchStatus === 'loading' ? '···' : '➜'}</button>
            </form>
            <input ref={fileRef} hidden type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => { const file = e.target.files?.[0]; if (file) { setImage(file); setNotice('이미지 인식(VLM)은 아직 연결되지 않았어. 영화 제목을 직접 입력해 줘.'); } }}/>
            {image && <div className="image-chip">첨부: {image.name}<button type="button" onClick={() => { setImage(null); if (fileRef.current) fileRef.current.value = ''; }}>×</button></div>}
            {notice && <p className="notice" role="alert">{notice}</p>}
          </section>
          {searchStatus === 'loading' && <p className="search-feedback">“{movie}” 촬영지 검색 중...</p>}
        </>}
        {view === 'results' && <>
          {searchStatus === 'empty' && <div className="empty-panel"><h2>“{movie}”의 촬영지를 찾지 못했어.</h2><p>공공데이터에 등록된 다른 영화로 다시 검색해 줘.</p><button type="button" className="route-button" onClick={reset}>새 영화 검색하기</button></div>}
          {searchStatus === 'success' && locations?.length > 0 && <FilmExplorer key={movie} locations={locations} movie={movie} onSave={storeCourse} onRecommend={storeRecommendedCourse} onReset={reset}/>}
        </>}
        {view === 'recent' && <SavedCourses courses={recommendedCourses} variant="recent" onOpen={openRecentCourse} onReset={reset}/>}
        {view === 'recent-detail' && openedCourse && <SavedCourseView course={openedCourse} heading="이전 추천 코스" onList={showRecommendedList} onReset={reset}/>}
        {view === 'saved' && <SavedCourses courses={savedCourses} onOpen={(record) => { setOpenedCourse(record); setView('saved-detail'); }} onDelete={deleteCourse} onReset={reset}/>}
        {view === 'saved-detail' && openedCourse && <SavedCourseView course={openedCourse} onList={showSavedList} onReset={reset}/>}
      </main>
      <footer className="footer">CineSpot 부산 · 영화 촬영지: 부산광역시·부산영상위원회 / 주변 장소·지도: 카카오맵</footer>
    </div>
  </div>;
}
