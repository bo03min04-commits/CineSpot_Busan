"""CineSpot 부산: 공공데이터 비밀키를 서버에서만 사용하는 FastAPI 프록시.

주의: 영화 속 부산정보 API의 실제 GET 기능 경로와 응답 필드명은
      공공데이터포털 '활용명세'에서 확인한 값으로 .env에 설정해야 합니다.
"""
import os
import re
import xml.etree.ElementTree as ET
from functools import lru_cache

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()
app = FastAPI(title="CineSpot Busan API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

API_URL = os.getenv("MOVIE_API_GET_URL", "").strip()
API_KEY = os.getenv("MOVIE_API_KEY", "").strip()
MAX_PAGES = min(max(int(os.getenv("MOVIE_MAX_PAGES", "5")), 1), 20)


def candidates(env_name, defaults):
    custom = os.getenv(env_name, "").strip()
    return ([custom] if custom else []) + defaults


TITLE_FIELDS = candidates("MOVIE_TITLE_FIELD", ["movieNm", "movieName", "movieTitle", "mvNm", "mviNm", "filmNm", "title", "영화명", "작품명"])
PLACE_FIELDS = candidates("MOVIE_PLACE_FIELD", ["placeNm", "placeName", "locationNm", "locNm", "filmPlace", "filmingPlace", "촬영장소", "장소명"])
ADDRESS_FIELDS = candidates("MOVIE_ADDRESS_FIELD", ["roadAddr", "roadNmAddr", "roadAddress", "address", "addr", "rdnmadr", "도로명주소", "주소"])
LAT_FIELDS = candidates("MOVIE_LAT_FIELD", ["lat", "latitude", "위도"])
LNG_FIELDS = candidates("MOVIE_LNG_FIELD", ["lng", "lon", "longitude", "경도"])
DESCRIPTION_FIELDS = candidates("MOVIE_DESCRIPTION_FIELD", ["description", "scene", "remark", "비고", "해설"])


def pick(row, field_names):
    # API 필드명이 확정되지 않았으므로, 설정 우선/기본 후보를 차례로 확인
    lowered = {str(key).lower(): key for key in row}
    for name in field_names:
        key = lowered.get(name.lower())
        if key is not None and row[key] not in (None, ""):
            return str(row[key]).strip()
    return ""


def get_rows(value):
    """공공데이터포털의 흔한 JSON 래핑 형식을 해제."""
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return value
        for child in value:
            rows = get_rows(child)
            if rows:
                return rows
    if isinstance(value, dict):
        for name in ("item", "row", "items", "rows", "records", "data", "list", "body", "response", "result"):
            if name in value:
                rows = get_rows(value[name])
                if rows:
                    return rows
        for child in value.values():
            rows = get_rows(child)
            if rows:
                return rows
        if any(field.lower() in {str(k).lower() for k in value} for field in TITLE_FIELDS):
            return [value]
    return []


def parse_page(response):
    # 오류 메시지에 인증키가 포함된 전체 URL이 노출되지 않도록 주의
    if response.status_code != 200:
        raise HTTPException(502, detail=f"공공데이터 API가 HTTP {response.status_code}을(를) 반환했습니다.")
    try:
        data = response.json()
        header = data.get("response", {}).get("header", {}) if isinstance(data, dict) else {}
        code = str(header.get("resultCode", "00"))
        if code not in ("00", "0"):
            raise HTTPException(502, detail=f"공공데이터 API 오류: {header.get('resultMsg', '응답 확인 필요')} (코드 {code})")
        return get_rows(data)
    except (ValueError, TypeError):
        pass
    try:
        root = ET.fromstring(response.content)
        code = root.findtext(".//resultCode")
        if code is not None and code not in ("00", "0"):
            raise HTTPException(502, detail=f"공공데이터 API 오류 코드: {code}")
        items = root.findall(".//item") or root.findall(".//row")
        return [{child.tag.split('}')[-1]: (child.text or "") for child in item} for item in items]
    except ET.ParseError:
        raise HTTPException(502, detail="공공데이터 응답이 JSON/XML 형식이 아닙니다. API GET 경로와 요청변수를 확인하세요.")


@lru_cache(maxsize=1)
def fetch_movie_rows():
    if not API_KEY or not API_URL or "실제" in API_URL:
        raise HTTPException(503, detail="backend/.env에 MOVIE_API_KEY와 실제 MOVIE_API_GET_URL을 먼저 설정하세요.")
    if not API_URL.startswith("https://apis.data.go.kr/6260000/"):
        raise HTTPException(503, detail="MOVIE_API_GET_URL을 승인받은 공공데이터 API의 실제 GET 요청주소로 설정하세요.")

    key_param = os.getenv("MOVIE_KEY_PARAM", "serviceKey")
    page_param = os.getenv("MOVIE_PAGE_PARAM", "pageNo")
    rows_param = os.getenv("MOVIE_ROWS_PARAM", "numOfRows")
    format_param = os.getenv("MOVIE_FORMAT_PARAM", "resultType")
    page_size = 100
    all_rows = []
    seen = set()
    for page in range(1, MAX_PAGES + 1):
        params = {key_param: API_KEY, page_param: page, rows_param: page_size}
        if format_param:
            params[format_param] = "json"
        try:
            response = requests.get(API_URL, params=params, timeout=15)
        except requests.RequestException:
            raise HTTPException(502, detail="공공데이터 API 통신에 실패했습니다. 요청주소·네트워크·승인 상태를 확인하세요.")
        rows = parse_page(response)
        # 응답 구조가 달라 빈 배열로 읽히는 경우도 있으므로 1페이지에서 알림
        if page == 1 and not rows:
            raise HTTPException(502, detail="API는 응답했지만 목록을 읽지 못했습니다. 실제 GET 응답 구조를 확인해야 합니다.")
        unique = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            signature = repr(sorted((str(k), str(v)) for k, v in row.items()))
            if signature not in seen:
                seen.add(signature)
                unique.append(row)
        if not unique:
            break
        all_rows.extend(unique)
        #if len(rows) < page_size:
            #break
        print("수집된 데이터 수:", len(row))
        print("영화 제목 목록:", sorted({
            pick(row, TITLE_FIELDS) for row in row
            if pick(row, TITLE_FIELDS)
        })[:30])
    return all_rows


def normalize(value):
    return re.sub(r"[\s\W_]+", "", value or "").casefold()


@app.get("/api/health")
def health():
    return {"ok": True, "movie_api_configured": bool(API_URL and API_KEY)}


@app.get("/api/filming-locations")
def filming_locations(movie: str = Query(min_length=1, max_length=100)):
    raw = fetch_movie_rows()
    print("실제 API 필드명:", list(raw[0].keys()) if raw else [])

    if not any(pick(row, TITLE_FIELDS) for row in raw):
        raise HTTPException(502, detail="영화 제목 필드를 찾지 못했습니다. 실제 응답의 필드명을 MOVIE_TITLE_FIELD에 설정하세요.")
    matches = []
    target = normalize(movie)
    for row in raw:
        title = pick(row, TITLE_FIELDS)
        place = pick(row, PLACE_FIELDS)
        if title and place and target in normalize(title):
            matches.append({
                "id": f"location-{len(matches) + 1}",
                "movie": title,
                "name": place,
                "address": pick(row, ADDRESS_FIELDS),
                "lat": pick(row, LAT_FIELDS) or None,
                "lng": pick(row, LNG_FIELDS) or None,
                "description": pick(row, DESCRIPTION_FIELDS),
            })
    if not matches and any(pick(row, TITLE_FIELDS) and target in normalize(pick(row, TITLE_FIELDS)) for row in raw):
        raise HTTPException(502, detail="영화는 검색되었지만 촬영장소 필드가 일치하지 않습니다. MOVIE_PLACE_FIELD를 확인하세요.")
    return {"movie": movie, "locations": matches, "source": "부산광역시_부산영상위원회 영화 속 부산정보"}
