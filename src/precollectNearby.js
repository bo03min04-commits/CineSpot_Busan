import { correctNearbyPlace } from './nearbyPlaceRules';
const RADIUS_METERS = 2000;
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
  const results = [];
  for (const category of ['FD6', 'AT4', 'CT1']) {
    await new Promise(resolve => setTimeout(resolve, 300));
    results.push(await categorySearch(kakao, category, anchor));
  }
  const [restaurants, attractions, culture] = results;
  const parking = [];
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
const bounded = (promise, ms) => new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('장소 검색 응답 시간 초과')), ms);
  Promise.resolve(promise).then(value => { clearTimeout(timer); resolve(value); }, error => { clearTimeout(timer); reject(error); });
});
window.cinespotPrecollect = {
  async prepare() {
    const key = import.meta.env.VITE_KAKAO_JAVASCRIPT_KEY;
    if (!key) throw new Error('루트 .env의 VITE_KAKAO_JAVASCRIPT_KEY를 확인해 주세요.');
    await bounded(loadKakaoSdk(key), 30000);
    return true;
  },
  async discover(location) {
    const position = await bounded(resolveFilmingPosition(window.kakao, location), 20000);
    if (!position) throw new Error('촬영지 위치를 확인하지 못했습니다.');
    const nearby = await bounded(loadNearby(window.kakao, position), 30000);
    const unique = new Map();
    for (const place of [...nearby.food, ...nearby.spot, ...nearby.parking]) {
      const id = String(place.id).replace(/^(food|spot|parking)-/, '');
      if (/^[0-9]{1,30}$/.test(id) && place.address) unique.set(id, { id, name: place.name, address: place.address, phone: place.phone || '' });
    }
    return { places: [...unique.values()], position };
  },
};
