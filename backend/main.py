"""CineSpot 부산: 공공데이터 비밀키를 서버에서만 사용하는 FastAPI 프록시.

주의: 영화 속 부산정보 API의 실제 GET 기능 경로와 응답 필드명은
      공공데이터포털 '활용명세'에서 확인한 값으로 .env에 설정해야 합니다.
"""
import os
import json
from pathlib import Path
import csv
import io
import re
import xml.etree.ElementTree as ET
from functools import lru_cache

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

load_dotenv()
app = FastAPI(title="CineSpot Busan API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

API_URL = os.getenv("MOVIE_API_GET_URL", "").strip()
API_KEY = os.getenv("MOVIE_API_KEY", "").strip()
MAX_PAGES = min(max(int(os.getenv("MOVIE_MAX_PAGES", "5")), 1), 20)


def candidates(env_name, defaults):
    custom = os.getenv(env_name, "").strip()
    return ([custom] if custom else []) + defaults


TITLE_FIELDS = candidates("MOVIE_TITLE_FIELD", ["movieNm", "moviNm", "moviName", "movieName", "movieTitle", "mvNm", "mviNm", "filmNm", "title", "영화명", "작품명"])
PLACE_FIELDS = candidates("MOVIE_PLACE_FIELD", ["placeNm", "place", "placeName", "plcNm", "locationNm", "locNm", "filmPlace", "filmingPlace", "촬영장소", "장소명"])
ADDRESS_FIELDS = candidates("MOVIE_ADDRESS_FIELD", ["roadAddr", "roadNmAddr", "roadNmAddress", "roadAddress", "address", "addr", "rdnmadr", "도로명주소", "주소"])
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


CINEMAP_URL = os.getenv("CINEMAP_API_GET_URL", "https://apis.data.go.kr/6260000/CineMapService/getCineMap").strip()
CINEMAP_KEY = os.getenv("CINEMAP_API_KEY", "").strip() or API_KEY


def fetch_source(url, key, source):
    if not url or not key:
        raise HTTPException(503, detail=f"{source} API 키 또는 요청주소가 설정되지 않았습니다.")
    if not url.startswith("https://apis.data.go.kr/6260000/"):
        raise HTTPException(503, detail=f"{source} 요청주소를 확인하세요.")
    key_param = os.getenv("MOVIE_KEY_PARAM", "serviceKey")
    page_param = os.getenv("MOVIE_PAGE_PARAM", "pageNo")
    rows_param = os.getenv("MOVIE_ROWS_PARAM", "numOfRows")
    format_param = os.getenv("MOVIE_FORMAT_PARAM", "resultType")
    page_size = 100
    all_rows, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        params = {key_param: key, page_param: page, rows_param: page_size}
        if format_param:
            params[format_param] = "json"
        try:
            response = requests.get(url, params=params, timeout=20)
        except requests.RequestException:
            raise HTTPException(502, detail=f"{source} API 통신에 실패했습니다.")
        rows = parse_page(response)
        if page == 1 and not rows:
            raise HTTPException(502, detail=f"{source} API 응답에서 목록을 찾지 못했습니다. 실제 응답 필드를 확인하세요.")
        unique = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            signature = repr(sorted((str(k), str(v)) for k, v in row.items()))
            if signature not in seen:
                seen.add(signature)
                unique.append(dict(row, _source=source))
        if not unique:
            break
        all_rows.extend(unique)
        if len(rows) < page_size:
            break
    return all_rows


@lru_cache(maxsize=1)
def fetch_movie_rows():
    return fetch_source(API_URL, API_KEY, "영화 속 부산정보")


@lru_cache(maxsize=1)
def fetch_cinemap_rows():
    return fetch_source(CINEMAP_URL, CINEMAP_KEY, "씨네맵")


def combined_rows():
    # 한 API가 일시적으로 실패해도 나머지 API의 결과는 제공한다.
    rows, errors = [], {}
    for source, fetcher in (("영화 속 부산정보", fetch_movie_rows), ("씨네맵", fetch_cinemap_rows)):
        try:
            rows.extend(fetcher())
        except HTTPException as exc:
            errors[source] = str(exc.detail)
    if not rows:
        raise HTTPException(502, detail={"message": "두 API에서 데이터를 가져오지 못했습니다.", "errors": errors})
    return rows, errors


# Title-level screening snapshot from the supplied 429-title export.
# This is a preliminary film candidate list, NOT a KOFIC-code verified catalog.
FILM_LIST_FILE = Path(__file__).resolve().parent / "data" / "verified_movies.json"

def allowed_movie_titles():
    try:
        return {normalize(t) for t in json.loads(FILM_LIST_FILE.read_text(encoding="utf-8"))}
    except (OSError, ValueError, TypeError):
        # Fail closed: never silently expose all TV series when the filter is missing.
        raise HTTPException(503, detail="영화 전용 목록 파일을 읽지 못했습니다. backend/data/verified_movies.json을 확인하세요.")

# Exclude locations explicitly marked as demolished or permanently closed.
# A missing marker does not prove a venue is still operating.
UNAVAILABLE_PLACE_MARKER = re.compile(
    r"\[\s*(?:철거|폐업|폐점|영업종료|운영종료|영구폐쇄|폐쇄)\s*\]"
    r"|(?:철거됨|철거완료|철거된\s*(?:장소|건물|시설)|폐업함|폐업한\s*(?:장소|업소|가게)|폐점함|영업\s*종료|운영\s*종료|영구\s*폐쇄)",
    re.IGNORECASE,
)


def is_unavailable_location(place, description=''):
    # Place names commonly contain [철거]/[폐업] markers.
    # Descriptions are checked only for explicit status language.
    return bool(UNAVAILABLE_PLACE_MARKER.search(str(place or '')) or
                UNAVAILABLE_PLACE_MARKER.search(str(description or '')))


def location_identity(name, address, lat, lng):
    """Conservative grouping: matching name AND grounded address/coordinates.

    Do not merge same-name places when their address is missing or differs.
    """
    n = normalize(name)
    addr = normalize(address)
    if addr:
        return (n, "address", addr)
    try:
        if lat not in (None, "") and lng not in (None, ""):
            return (n, "coordinates", round(float(lat), 5), round(float(lng), 5))
    except (TypeError, ValueError):
        pass
    return None


def unified_locations():
    rows, errors = combined_rows()
    allowed = allowed_movie_titles()
    grouped = {}
    blocked = set()
    for row in rows:
        title = pick(row, TITLE_FIELDS)
        name = pick(row, PLACE_FIELDS)
        if not title or not name or normalize(title) not in allowed:
            continue
        address = pick(row, ADDRESS_FIELDS)
        lat, lng = pick(row, LAT_FIELDS), pick(row, LNG_FIELDS)
        description = pick(row, DESCRIPTION_FIELDS)
        identity = location_identity(name, address, lat, lng)
        # Rows without a verifiable location are retained separately per movie.
        key = identity or ("unresolved", normalize(name), normalize(title))
        if is_unavailable_location(name, description):
            blocked.add(key)
            grouped.pop(key, None)
            continue
        if key in blocked:
            continue
        loc = grouped.get(key)
        if loc is None:
            loc = {"id": "", "movie": title, "movies": [title], "name": name,
                   "address": address, "lat": lat or None, "lng": lng or None,
                   "description": description, "sources": [row["_source"]]}
            grouped[key] = loc
        else:
            if normalize(title) not in {normalize(m) for m in loc["movies"]}:
                loc["movies"].append(title)
            for field, value in (("address", address), ("lat", lat), ("lng", lng),
                                 ("description", description)):
                if not loc[field] and value:
                    loc[field] = value
            if row["_source"] not in loc["sources"]:
                loc["sources"].append(row["_source"])
    result = list(grouped.values())
    for index, loc in enumerate(result, 1):
        loc["id"] = f"place-{index}"
        loc["movies"].sort(key=str.casefold)
        loc["movie"] = loc["movies"][0]  # compatibility for older UI consumers
    return result, errors


def normalize(value):
    return re.sub(r"[\s\W_]+", "", value or "").casefold()


@app.get("/api/health")
def health():
    return {"ok": True, "movie_api_configured": bool(API_URL and API_KEY)}


@app.get("/api/filming-locations")
def filming_locations(movie: str = Query(min_length=1, max_length=100)):
    locations, errors = unified_locations()
    target = normalize(movie)
    matches = [loc for loc in locations if any(target in normalize(m) for m in loc["movies"])]
    return {"movie": movie, "locations": matches,
            "source": "부산영상위원회 영화 속 부산정보 + 씨네맵", "source_errors": errors}


@app.get("/api/all-filming-locations")
def all_filming_locations():
    locations, errors = unified_locations()
    return {"locations": locations,
            "source": "부산영상위원회 영화 속 부산정보 + 씨네맵", "source_errors": errors}


@app.get("/api/movie-filter-status")
def movie_filter_status():
    directory = Path(__file__).resolve().parent / "data"
    return {"method": "제목 기반 1차 분류", "kofic_verified": False,
            "film_candidates": len(json.loads((directory / "verified_movies.json").read_text(encoding="utf-8"))),
            "excluded": len(json.loads((directory / "excluded_titles.json").read_text(encoding="utf-8"))),
            "review_required": len(json.loads((directory / "review_required.json").read_text(encoding="utf-8")))}

@app.get("/api/movies")
def all_movies():
    locations, errors = unified_locations()
    titles = sorted({title for loc in locations for title in loc["movies"]}, key=lambda x: x.casefold())
    return {"count": len(titles), "movies": titles, "source_errors": errors}


@app.get("/api/movies.csv")
def movies_csv():
    locations, _ = unified_locations()
    titles = sorted({title for loc in locations for title in loc["movies"]}, key=lambda x: x.casefold())
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["영화 제목"])
    writer.writerows([[title] for title in titles])
    return StreamingResponse(iter(["\ufeff" + output.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=cinespot_movies.csv"})


@app.get("/api/locations.csv")
def locations_csv():
    locations, _ = unified_locations()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["영화 제목", "촬영지", "주소", "위도", "경도", "출처"])
    for loc in locations:
        writer.writerow([", ".join(loc["movies"]), loc["name"], loc["address"], loc["lat"], loc["lng"], ", ".join(loc["sources"])])
    return StreamingResponse(iter(["\ufeff" + output.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=cinespot_locations.csv"})


# Reference images are local, not fetched from third-party sites.
from poster_matcher import match_poster, poster_catalog, POSTER_DIR

@app.get("/api/poster-catalog")
def get_poster_catalog():
    return {"movies": poster_catalog(), "reference_directory": "backend/posters", "method": "perceptual image hash"}

@app.post("/api/recognize-poster")
async def recognize_poster(image: UploadFile = File(...)):
    """Compare an upload against locally registered poster versions; no paid API."""
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(400, detail="JPG, PNG, WEBP 포스터만 업로드할 수 있습니다.")
    content = await image.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, detail="이미지 크기는 5MB 이하여야 합니다.")
    try:
        result = match_poster(content)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    if not result["candidates"]:
        raise HTTPException(422, detail="제목을 직접 검색해 주세요.")
    return result
