import { correctNearbyPlace } from './nearbyPlaceRules';
import PlaceOperatingInfo from './PlaceOperatingInfo';
import { useEffect, useMemo, useRef, useState } from 'react';
import MovieRecommendations from './MovieRecommendations';
import { shareCoursePdf } from './coursePdfShare';
import PdfShareFallbackDialog from './PdfShareFallbackDialog';
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
      const place = correctNearbyPlace(toKakaoPlace(row, kind, anchor));
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

function FilmedMovies({ movies, currentMovie, onMovieSearch }) {
  const titles = [...new Set((movies || []).filter(Boolean))];
  if (!titles.length) return null;
  return <div className="filmed-movies">
    <strong>이 장소에서 촬영된 영화 · {titles.length}편</strong>
    <div className="filmed-movie-links">{titles.map(title =>
      onMovieSearch && title !== currentMovie
        ? <button type="button" key={title} className="filmed-movie-tag" onClick={() => onMovieSearch(title)}>{title}</button>
        : <span key={title} className="filmed-movie-tag">{title}</span>
    )}</div>
  </div>;
}

function FilmExplorer({ movie, locations, onReset, onSave, onRecommend, onMovieSearch }) {
  const featuredLocations = useMemo(() => featuredLocationsFor(movie), [movie]);
  // 대표 명소가 없는 영화만 공공데이터의 실제 촬영지 목록으로 대체합니다.
  // 주소 또는 명시적 좌표가 없는 항목은 목록에는 남기되 지도/코스는 비활성화합니다.
  const isPublicFallback = featuredLocations.length === 0;
  const displayedLocations = useMemo(() => isPublicFallback
    ? locations.map((place, index) => ({
        ...place,
        id: place.id || `public-${index}`,
        name: place.name || place.place || place.placeName || '이름 미확인 촬영지',
        filmRelation: '공공데이터 등록 촬영지',
      }))
    : featuredLocations, [isPublicFallback, locations, featuredLocations]);
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
  const [pdfShareData, setPdfShareData] = useState(null);
  const [currentRecommendation, setCurrentRecommendation] = useState(null);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    setResolving(true);
    (async () => {
      const resolved = new Array(displayedLocations.length).fill(null);
      for (let i = 0; i < displayedLocations.length; i += 3) {
        const batch = await Promise.all(displayedLocations.slice(i, i + 3)
          .map((place) => {
            // 공공데이터에는 이름만 있는 장소도 있습니다. 장소명으로 좌표를 추정하지 않습니다.
            if (isPublicFallback && !place.address && (place.lat == null || place.lng == null)) return null;
            return resolveFilmingPosition(window.kakao, isPublicFallback ? { ...place, name: '', kakaoKeyword: '' } : place);
          }));
        if (cancelled) return;
        batch.forEach((position, idx) => { resolved[i + idx] = position; });
        setPositions([...resolved]); // 순차 결과를 즉시 화면에 반영
      }
      if (!cancelled) setResolving(false);
    })().catch((err) => { if (!cancelled) { setResolving(false); setNearbyError(err.message); } });
    return () => { cancelled = true; };
  }, [ready, displayedLocations, isPublicFallback]);

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
    ? { ...anchor, id: `film-${filmIndex}`, name: displayedLocations[filmIndex].name, address: displayedLocations[filmIndex].address || '', kind: 'film' }
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
  function sharePdfCourse() {
    const record = currentRecommendation || createCourseRecord();
    const mapUrl = kakaoCourseUrl(record.route) || (record.route?.[0] ? kakaoDestinationUrl(record.route[0]) : '');
    shareCoursePdf(record, mapUrl, setSaveMessage, setPdfShareData);
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
      {mode === 'explore' && <span className="result-count">{isPublicFallback ? '공공데이터 촬영지' : '대표 명소'} {displayedLocations.length}곳</span>}
    </div>
    <div className="stepbar" aria-label="진행 단계">
      <span className={mode === 'explore' ? 'current' : ''}>1 촬영지 선택</span>
      <span className={mode === 'choose' ? 'current' : ''}>2 주변 탐방</span>
      <span className={mode === 'route' ? 'current' : ''}>3 추천 코스</span>
    </div>

    {mode === 'explore' && <>
      <div className="planner-card film-pick-screen">
        <div className="section-head"><h3>{isPublicFallback ? '공공데이터 영화 촬영지' : '대표 영화 명소'}</h3><span>{isPublicFallback ? '등록' : '엄선'} {displayedLocations.length}곳</span></div>

        {isPublicFallback && <div className="featured-empty">
          <strong>이 영화의 대표 명소는 아직 선정되지 않았어.</strong>
          <p>대신 공공데이터에 등록된 촬영 장소 {locations.length}곳을 보여줄게. 아래에서 장소를 선택하면 위치가 확인된 경우에만 주변 맛집·관광지·주차장을 찾을 수 있어.</p>
          <p>주소나 좌표가 없거나 지도에서 주소를 확인하지 못한 장소는 위치 확인이 필요해.</p>
        </div>}
        <div className="film-list">
          {displayedLocations.map((place, i) => (
            <div className="film-entry" key={`${place.id}-${i}`}>
              <button type="button" className={`film-choice ${filmIndex === i ? 'active' : ''}`}
                aria-expanded={filmIndex === i} aria-controls={`inline-map-${i}`}
                onClick={() => selectFilm(i)}>
                <span className="film-no">{String(i + 1).padStart(2, '0')}</span>
                <span className="film-name"><strong>{place.name}</strong>
                  <small>{place.filmRelation}</small>
                  <small>{(place.movies || locations.find(p => p.name === place.name)?.movies || [movie]).join(" · ")}</small>
                  <small>{positions[i] ? '장소 위치 보기 · 주변 탐방 가능' : resolving ? '위치 확인 중...' : '주소·좌표 확인 필요 · 지도 표시 불가'}</small>
                </span>
                <span className="choice-chevron">{filmIndex === i ? '접기 −' : '지도 보기 ＋'}</span>
              </button>
              {filmIndex === i && <div id={`inline-map-${i}`} className="inline-map-wrap">
                {ready && positions[i] ? <>
                  <SingleLocationMap key={`film-map-${i}`} place={{ ...positions[i], name: place.name, id: place.id }}/>
                  <div className="map-selection-note"><strong>{place.name}</strong>
                    <span>{place.note}</span>
                    <FilmedMovies movies={place.movies || locations.find(p => p.name === place.name)?.movies || [movie]} currentMovie={movie} onMovieSearch={onMovieSearch}/>
                    {positions[i].estimated && <span>카카오맵 장소 검색 위치</span>}
                    <div className="featured-links">{place.sourceUrl && <a href={place.sourceUrl} target="_blank" rel="noreferrer">선정 근거 보기 ↗</a>}
                    <a href={kakaoDestinationUrl({ ...positions[i], name: place.name })} target="_blank" rel="noreferrer">카카오맵에서 크게 보기 ↗</a></div>
                  </div>
                </> : <p className="inline-map-pending">{resolving ? '카카오맵에서 이 촬영지의 위치를 찾고 있어...' : '주소 또는 좌표를 확인하지 못했어. 이 촬영지는 지도에 표시하지 않아. 다른 촬영지를 선택해 줘.'}</p>}
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
          {/* Only restaurant tab mounts operating information. */}
          {tab === 'food' && <PlaceOperatingInfo place={place}/>}
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
          <button type="button" className="secondary-button" onClick={sharePdfCourse}>PDF 공유하기</button>
          <button type="button" className="secondary-button" onClick={() => moveMode('choose')}>장소 다시 고르기</button>
          <button type="button" className="secondary-button" onClick={onReset}>재검색하기</button>
        </div>
        {saveMessage && <p className="save-notice" role="status">{saveMessage}</p>}
      </div>
    </>}
    {pdfShareData && <PdfShareFallbackDialog data={pdfShareData} onClose={() => setPdfShareData(null)} />}
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
  const [shareMessage, setShareMessage] = useState('');
  const [pdfShareData, setPdfShareData] = useState(null);
  function shareSavedCourse() {
    const mapUrl = kakaoCourseUrl(course.route) || (course.route?.[0] ? kakaoDestinationUrl(course.route[0]) : '');
    shareCoursePdf(course, mapUrl, setShareMessage, setPdfShareData);
  }
  return <section className="results">
    <div className="results-title"><div><span className="eyebrow">{heading}</span><h2>{course.title}</h2><p>생성일 {new Date(course.createdAt).toLocaleString('ko-KR')}</p></div></div>
    {error ? <p role="alert" className="notice">{error}</p> : <RouteDisplay route={course.route} ready={ready}/>}
    <div className="bottom-actions"><div className="action-row"><button type="button" className="route-button" onClick={shareSavedCourse}>PDF 공유하기</button><button type="button" className="secondary-button" onClick={onList}>← 코스 목록</button><button type="button" className="route-button" onClick={onReset}>재검색하기</button></div>{shareMessage && <p className="save-notice" role="status">{shareMessage}</p>}</div>
    {pdfShareData && <PdfShareFallbackDialog data={pdfShareData} onClose={() => setPdfShareData(null)} />}
  </section>;
}

const RECOMMENDATIONS_KEY = 'cinespot-busan-recommended-courses-v1';
function readRecommendedCourses() {
  try {
    const entries = JSON.parse(window.localStorage.getItem(RECOMMENDATIONS_KEY) || '[]');
    return Array.isArray(entries) ? entries.filter((item) => item && Array.isArray(item.route) && item.route.length) : [];
  } catch { return []; }
}


const BUSAN_DISTRICTS = ['전체', '강서구', '금정구', '기장군', '남구', '동구', '동래구', '부산진구', '북구', '사상구', '사하구', '서구', '수영구', '연제구', '영도구', '중구', '해운대구'];

function LocationExplorer({ onMovieSearch, onBack }) {
  const { ready, error: mapError } = useKakaoReady();
  const [district, setDistrict] = useState('전체');
  const [places, setPlaces] = useState([]);
  const [status, setStatus] = useState('loading');
  const [message, setMessage] = useState('');
  const [myPosition, setMyPosition] = useState(null);
  const [nearOnly, setNearOnly] = useState(false);
  const [radius, setRadius] = useState(5);
  const [resolved, setResolved] = useState([]);
  const [resolving, setResolving] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 24;

  useEffect(() => {
    const controller = new AbortController();
    setStatus('loading');
    fetch(`${API_BASE}/api/all-filming-locations`, { signal: controller.signal })
      .then(async (r) => {
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || '촬영지 데이터 조회 실패');
        setPlaces(data.locations || []);
        setStatus('ready');
        if (data.source_errors?.length) setMessage('일부 공공데이터를 불러오지 못했습니다. 검색 결과가 누락될 수 있습니다.');
      })
      .catch(e => { if (e.name !== 'AbortError') { setMessage(e.message); setStatus('error'); } });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!ready || status !== 'ready') return;
    let cancelled = false;
    setResolved([]);
    setResolving(true);
    (async () => {
      const valid = places.filter(place => place.address || (place.lat != null && place.lng != null));
      const output = [];
      // Geocoding calls are batched to avoid a long serial wait.
      for (let i = 0; i < valid.length; i += 5) {
        if (cancelled) return;
        const batch = await Promise.all(valid.slice(i, i + 5).map(async place => {
          try {
            // Disable keyword-based guesses; use only dataset coordinates or address.
            const pos = await resolveFilmingPosition(window.kakao, { ...place, name: '', kakaoKeyword: '' });
            if (!pos) return null;
            const address = place.address || pos.resolvedAddress || '';
            const region = BUSAN_DISTRICTS.find(d => d !== '전체' && address.includes(d)) || '';
            return { ...place, ...pos, region };
          } catch { return null; }
        }));
        output.push(...batch.filter(Boolean));
        if (!cancelled) setResolved([...output]);
      }
      if (!cancelled) setResolving(false);
    })().catch(e => { if (!cancelled) { setResolving(false); setMessage(e.message); } });
    return () => { cancelled = true; };
  }, [ready, places, status]);

  function locateMe() {
    if (!navigator.geolocation) { setMessage('이 브라우저는 위치 기능을 지원하지 않습니다.'); return; }
    setMessage('현재 위치를 확인하고 있습니다...');
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        setMyPosition({ lat: coords.latitude, lng: coords.longitude });
        setNearOnly(true);
        setDistrict('전체');
        setPage(1);
        setMessage('현재 위치 기준 직선거리로 정렬했습니다. 이동 시간이나 실제 경로 거리는 아닙니다.');
      },
      (error) => {
        const descriptions = {
          1: '위치 접근이 거부되었습니다. 브라우저 사이트 권한에서 위치를 허용해 주세요.',
          2: '현재 위치를 확인할 수 없습니다. 기기 위치 서비스를 확인해 주세요.',
          3: '위치 확인 시간이 초과되었습니다. 다시 시도해 주세요.',
        };
        setMessage(descriptions[error.code] || '위치 확인에 실패했습니다. HTTPS 또는 localhost에서 다시 시도해 주세요.');
      },
      { enableHighAccuracy: false, timeout: 12000, maximumAge: 60000 }
    );
  }

  const shown = useMemo(() => resolved
    .filter(p => district === '전체' || p.region === district)
    .map(p => ({ ...p, distance: myPosition ? haversine(myPosition, p) : null }))
    .filter(p => !nearOnly || !myPosition || p.distance <= radius * 1000)
    .sort((a, b) => myPosition ? a.distance - b.distance : a.name.localeCompare(b.name, 'ko')),
    [resolved, district, myPosition, nearOnly, radius]);
  const selected = shown.find(p => p.id === selectedId) || null;
  const pageCount = Math.max(1, Math.ceil(shown.length / PAGE_SIZE));
  const visible = shown.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  return <section className="results location-explorer">
    <div className="results-title"><div><span className="eyebrow">CINESPOT · BUSAN</span><h2>부산 영화 촬영지 탐색</h2><p>내 위치 주변 또는 부산 구·군별 영화 촬영지를 찾아보세요.</p></div></div>
    <div className="planner-card">
      <div className="location-controls">
        <label>지역 선택 <select value={district} onChange={e => { setDistrict(e.target.value); setPage(1); setSelectedId(null); }}>{BUSAN_DISTRICTS.map(d => <option key={d}>{d}</option>)}</select></label>
        <button type="button" className="route-button" onClick={locateMe}>⌖ 내 위치로 주변 촬영지 찾기</button>
        {myPosition && <>
          <label>반경 <select value={radius} onChange={e => { setRadius(Number(e.target.value)); setPage(1); }}>{[1, 2, 5, 10, 20].map(n => <option value={n} key={n}>{n}km</option>)}</select></label>
          <label className="location-checkbox"><input type="checkbox" checked={nearOnly} onChange={e => { setNearOnly(e.target.checked); setPage(1); }}/> 반경 내만 보기</label>
          <button type="button" className="secondary-button" onClick={() => { setMyPosition(null); setNearOnly(false); setPage(1); setMessage('위치 기반 필터를 해제했습니다.'); }}>위치 필터 해제</button>
        </>}
      </div>
      {message && <p className="minor-note" role="status">{message}</p>}
      {mapError && <p className="warning-note">{mapError}</p>}
      {status === 'loading' && <p>공공 촬영지 데이터를 불러오는 중...</p>}
      {status === 'error' && <p className="warning-note">촬영지 목록을 불러오지 못했습니다. 백엔드 실행 상태를 확인해 주세요.</p>}
      {status === 'ready' && <>
        <p className="source-line">위치 확인된 영화 촬영지 {shown.length}곳{resolving ? ' · 위치 확인 진행 중…' : ''} · 확인되지 않은 주소/좌표는 제외 · 거리는 직선거리</p>
        {selected && <div className="location-selected-map">
          <div className="section-head"><h3>{selected.name}</h3><button type="button" className="secondary-button" onClick={() => setSelectedId(null)}>지도 닫기 ×</button></div>
          <FilmedMovies movies={selected.movies || [selected.movie]} onMovieSearch={onMovieSearch}/>
          <SingleLocationMap key={selected.id} place={selected}/>
          <div className="action-row"><a href={kakaoDestinationUrl(selected)} target="_blank" rel="noreferrer">카카오맵에서 보기 ↗</a></div>
        </div>}
        <div className="location-results">{visible.map(p => <article className={`location-card ${selectedId === p.id ? 'selected' : ''}`} key={p.id}>
          <strong>{p.name}</strong>
          <span>{p.region || '지역 미확인'}{p.distance !== null ? ` · ${formatMeters(p.distance)}` : ''}</span>
          <span>촬영 영화 {p.movies?.length || 1}편 · {(p.movies || [p.movie]).join(" · ")}</span>
          <small>{p.address || '공공데이터 좌표 제공'}</small>
          <div className="action-row"><button type="button" className="secondary-button" onClick={() => setSelectedId(p.id)}>지도에서 보기</button></div>
        </article>)}</div>
        {pageCount > 1 && <div className="location-pagination">
          <button type="button" className="secondary-button" disabled={page === 1} onClick={() => setPage(p => p - 1)}>이전</button>
          <span>{page} / {pageCount} 페이지</span>
          <button type="button" className="secondary-button" disabled={page === pageCount} onClick={() => setPage(p => p + 1)}>다음</button>
        </div>}
        {!shown.length && <p className="places-status">{resolving ? '촬영지 좌표를 확인하고 있습니다…' : '조건에 맞는 위치 확인 촬영지가 없습니다. 지역 또는 반경을 변경해 보세요.'}</p>}
      </>}
      <p className="source-line">촬영지: 부산광역시·부산영상위원회 공공데이터 / 위치 확인: 카카오맵 · 영화 전용 목록은 제목 기반 1차 분류입니다.</p>
    </div>
    <div className="bottom-actions"><button className="secondary-button" type="button" onClick={onBack}>← 영화 검색으로</button></div>
  </section>;
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
  const [posterLoading, setPosterLoading] = useState(false);
  const [posterCandidates, setPosterCandidates] = useState([]);
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
  async function recognizePoster(file) {
    if (!file) return;
    setImage(file);
    setPosterCandidates([]);
    if (file.size > 5 * 1024 * 1024) { setNotice('포스터 이미지는 5MB 이하여야 합니다.'); return; }
    setPosterLoading(true);
    setNotice('저장된 포스터 이미지와 비교 중입니다...');
    try {
      const form = new FormData();
      form.append('image', file);
      const response = await fetch(`${API_BASE}/api/recognize-poster`, { method: 'POST', body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `이미지 비교 오류 (HTTP ${response.status})`);
      const candidates = Array.isArray(data.candidates) ? data.candidates : [];
      setPosterCandidates(candidates);
      if (!candidates.length) throw new Error('등록된 포스터와 유사한 이미지를 찾지 못했어요.');
      setQuery(candidates[0].title);
      setNotice('인식된 영화 제목이 맞는지 확인한 후 검색해 주세요.');
    } catch (e) { setNotice(e.message || '포스터 비교에 실패했습니다.'); }
    finally { setPosterLoading(false); }
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
    setPosterCandidates([]);
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

  return <div className={`app-shell view-${view} ${darkMode ? 'dark' : ''}`}>
    {sidebarOpen && <button type="button" className="sidebar-scrim" aria-label="사이드바 닫기" onClick={() => setSidebarOpen(false)}/>}
    <aside className={`app-sidebar ${sidebarOpen ? 'sidebar-open' : ''}`} aria-label="추천 코스 사이드바">
      <div className="sidebar-header"><button className="sidebar-brand" type="button" onClick={reset}><span className="sidebar-logo-icon" aria-hidden="true">▣</span>CineSpot<span>부산</span></button>
        <button className="sidebar-close" type="button" aria-label="사이드바 닫기" onClick={() => setSidebarOpen(false)}>×</button></div>
      <button type="button" className="new-chat-button" onClick={reset}><span aria-hidden="true">＋</span> 새 채팅</button>
      <button type="button" className={`sidebar-shortcut ${view === 'saved' || view === 'saved-detail' ? 'selected' : ''}`} onClick={showSavedList}>
        <span aria-hidden="true">▤</span> 저장한 코스 <small>{savedCourses.length}</small>
      </button>
      <button type="button" className={`sidebar-shortcut ${view === 'location-explorer' ? 'selected' : ''}`} onClick={() => { setView('location-explorer'); setSidebarOpen(false); }}>⌖ 주변·지역별 촬영지</button>
      <div className="sidebar-history-heading"><button type="button" className="history-heading-button" onClick={showRecommendedList}>이전 추천 코스</button>
        {!!recommendedCourses.length && <button type="button" onClick={clearRecommendations}>모두 지우기</button>}</div>
      <nav className="history-list" aria-label="이전 추천 코스 목록">
        {!recommendedCourses.length && <p className="history-empty">코스를 만들면 여기에 자동으로 표시돼.</p>}
        {recommendedCourses.map((entry) => <button key={entry.id} type="button" title={`${entry.title} 다시 보기`}
          className={`history-item ${view === 'recent-detail' && openedCourse?.id === entry.id ? 'selected' : ''}`}
          onClick={() => openRecentCourse(entry)}><span aria-hidden="true">◷</span><span>{entry.title}</span></button>)}
      </nav>
      <div className="sidebar-scenery" aria-hidden="true" />
      <div className="sidebar-bottom">영화 촬영지 · 주변 탐방 · 나만의 코스</div>
    </aside>
    <div className="app-main">
      <header className="topbar">
        <div className="topbar-left"><button type="button" className="sidebar-toggle" aria-label="메뉴 열기" onClick={() => setSidebarOpen(true)}>☰</button>
          <button className="wordmark" type="button" onClick={reset}>CineSpot<span>부산</span></button></div>
        <div className="topbar-actions"><button className="theme-button" type="button" onClick={() => setDarkMode((v) => !v)}>{darkMode ? '☀ 라이트' : '☾ 다크'}</button></div>
      </header>
      <main className="page-content">
        {view !== 'search' && <section className="scene-banner" aria-label="CineSpot 부산 영화 여행">
          <div className="scene-banner-inner"><span className="scene-banner-eyebrow">CINESPOT · BUSAN FILM TOUR</span><h1>영화 속 부산을 여행하다</h1><p>촬영지부터 나만의 여행 코스까지</p></div>
        </section>}
        {view === 'search' && <>
          <section className="hero" aria-label="영화 검색">
            <span className="hero-film-icon" aria-hidden="true"><svg viewBox="0 0 64 60"><path d="M8 21h48v33H8Z" fill="#2e81dc" stroke="#fff" strokeWidth="3"/><path d="M9 20L53 9l4 11-44 12Z" fill="#fff" stroke="#2e81dc" strokeWidth="3"/><path d="M17 18l9 10M32 14l8 10M46 10l8 10" stroke="#2e81dc" strokeWidth="4"/><path d="M27 30l14 8-14 9Z" fill="#fff"/></svg></span>
            <div className="hero-kicker">BUSAN · MOVIE &amp; TRAVEL</div>
            <h1>CineSpot <span>부산</span></h1>
            <p className="hero-subtitle">어떤 부산 영화가 궁금하신가요?</p>
            <form className="searchbar" onSubmit={handleSearch}>
              <div className="add-menu-container" ref={actionMenuRef}>
                <button type="button" className={`search-icon-button add-button ${actionMenuOpen ? 'is-open' : ''}`}
                  aria-label="추가 메뉴" aria-expanded={actionMenuOpen} onClick={() => setActionMenuOpen((v) => !v)}><svg className="add-plus-icon" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M12 4v16M4 12h16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/></svg></button>
                {actionMenuOpen && <div className="add-menu">
                  <button type="button" className="add-menu-item" onClick={() => { setActionMenuOpen(false); fileRef.current?.click(); }}>▧ 이미지 추가</button>
                  <button type="button" className="add-menu-item" onClick={() => { setActionMenuOpen(false); setNotice('웹 검색 기능은 아직 연결되지 않았어. 영화 제목 검색 후 코스 추천에서 주변 장소를 확인할 수 있어.'); }}>◎ 웹 검색</button>
                </div>}
              </div>
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="영화 제목을 입력하세요 (예: 국제시장)" aria-label="영화 제목" />
              <button type="submit" className="submit-button" disabled={searchStatus === 'loading'} aria-label="검색하기">{searchStatus === 'loading' ? '···' : '➜'}</button>
            </form>
            <div className="hero-quick-actions">
              <button type="button" onClick={() => { setView('location-explorer'); setSidebarOpen(false); }}><span aria-hidden="true">⌖</span> 내 주변 촬영지</button>
              <button type="button" onClick={() => fileRef.current?.click()}><span aria-hidden="true">▣</span> 영화 포스터 추가</button>
              <button type="button" onClick={showRecommendedList}><span aria-hidden="true">↗</span> 이전 추천 코스</button>
            </div>
            <input ref={fileRef} hidden type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => { const file = e.target.files?.[0]; if (file) recognizePoster(file); }}/>
            {image && <div className="image-chip">첨부: {image.name}<button type="button" onClick={() => { setImage(null); if (fileRef.current) fileRef.current.value = ''; }}>×</button></div>}
            {notice && <p className="notice" role="alert">{notice}</p>}
            {posterCandidates.length > 0 && <div className="poster-candidates" aria-label="포스터 일치 후보">
              <strong>포스터 이미지 일치 후보 (선택 후 검색)</strong>
              {posterCandidates.map(candidate => <button type="button" className="secondary-button" key={candidate.title}
                onClick={() => { setQuery(candidate.title); setNotice(`“${candidate.title}” 선택됨. 검색 버튼을 눌러 촬영지를 확인하세요.`); }}>
                {candidate.title}
              </button>)}
            </div>}
          </section>
          <MovieRecommendations
  apiBase={API_BASE}
  onMovieSearch={searchMovie}
  searchLoading={searchStatus === 'loading'}
/>
          {posterLoading && <p className="search-feedback">포스터 인식 중...</p>}
          {searchStatus === 'loading' && <p className="search-feedback">“{movie}” 촬영지 검색 중...</p>}
        </>}
        {view === 'location-explorer' && <LocationExplorer onMovieSearch={searchMovie} onBack={reset}/>}
        {view === 'results' && <>
          {searchStatus === 'empty' && <div className="empty-panel"><h2>“{movie}”의 촬영지를 찾지 못했어.</h2><p>공공데이터에 등록된 다른 영화로 다시 검색해 줘.</p><button type="button" className="route-button" onClick={reset}>새 영화 검색하기</button></div>}
          {searchStatus === 'success' && locations?.length > 0 && <FilmExplorer key={movie} locations={locations} movie={movie} onSave={storeCourse} onRecommend={storeRecommendedCourse} onReset={reset} onMovieSearch={searchMovie}/>}
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
