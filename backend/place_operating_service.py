"""Shared operating information and conservative public-data matching.

Public sources do not establish whether a business is currently open or closed.
Manual information is authoritative until explicitly removed by an editor.
"""
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import unquote

import requests
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal

KST = timezone(timedelta(hours=9))
TOUR_BASE = 'https://apis.data.go.kr/B551011/KorService2/'
PARKING_URL = 'https://apis.data.go.kr/6260000/BusanPblcPrkngInfoService/getPblcPrkngInfo'
TOUR_SOURCE = 'https://www.data.go.kr/data/15101578/openapi.do'
PARKING_SOURCE = 'https://www.data.go.kr/data/15004683/openapi.do'


def today():
    return datetime.now(KST).date().isoformat()


class Info(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    hours: str = Field(default='', max_length=2000)
    closedDays: str = Field(default='', max_length=500)
    status: Literal['unknown', 'operating', 'temporarily_closed', 'permanently_closed'] = 'unknown'
    source: str = Field(min_length=1, max_length=1000)
    checkedOn: date

    @field_validator('checkedOn')
    @classmethod
    def check_date(cls, value):
        if value.isoformat() > today():
            raise ValueError('확인일은 오늘 이후일 수 없습니다.')
        return value


class Place(BaseModel):
    model_config = ConfigDict(extra='ignore', str_strip_whitespace=True)
    id: str = Field(pattern=r'^[0-9]{1,30}$')
    name: str = Field(min_length=1, max_length=200)
    kind: Literal['food', 'spot', 'parking']
    address: str = Field(default='', max_length=500)
    phone: str = Field(default='', max_length=60)
    lat: float = Field(ge=34.7, le=35.7, allow_inf_nan=False)
    lng: float = Field(ge=128.5, le=129.8, allow_inf_nan=False)


class SaveRequest(BaseModel):
    place_id: str = Field(pattern=r'^[0-9]{1,30}$')
    info: Info
    expected_revision: int = Field(ge=0)


class RemoveRequest(BaseModel):
    place_id: str = Field(pattern=r'^[0-9]{1,30}$')
    expected_revision: int = Field(ge=0)


class SourceError(Exception):
    pass


def plain(value):
    value = re.sub(r'(?i)<br\s*/?>|</p>', '\n', str(value or ''))
    return unescape(re.sub(r'<[^>]*>', '', value)).strip()


def norm(value):
    return re.sub(r'[^0-9a-z가-힣]', '', plain(value).casefold())


def address_norm(value):
    # Preserve road numbers; only remove equivalent city prefixes and spacing.
    return norm(value).replace('부산광역시', '부산', 1)


def distance(place, row):
    try:
        lat, lng = float(row['lat']), float(row['lng'])
        if not math.isfinite(lat) or not math.isfinite(lng):
            return math.inf
        a = math.sin(math.radians(lat - place.lat) / 2) ** 2
        a += math.cos(math.radians(lat)) * math.cos(math.radians(place.lat)) * math.sin(math.radians(lng - place.lng) / 2) ** 2
        return 6371000 * 2 * math.asin(min(1, math.sqrt(a)))
    except (KeyError, TypeError, ValueError):
        return math.inf


def unique_match(place, rows):
    matches = {}
    for row in rows:
        # Exact normalized titles keep different branches and nearby businesses apart.
        if not norm(place.name) or norm(row.get('name')) != norm(place.name):
            continue
        gap = distance(place, row)
        same_address = bool(place.address and row.get('address')) and address_norm(place.address) == address_norm(row['address'])
        if gap > 80 and not (same_address and gap < 300):
            continue
        matches[str(row['id'])] = row
    return next(iter(matches.values())) if len(matches) == 1 else None


def api_error(stage, code):
    # Only a short result code is exposed, never a raw upstream body or URL.
    code = str(code if code is not None else '').strip()
    safe_code = code if re.fullmatch(r'[A-Za-z0-9_-]{1,24}', code) else '없음'
    return SourceError(f'{stage}: API 응답 오류 (코드 {safe_code}).')


def upstream_items(payload, body_name=None, stage='공공데이터 조회'):
    if not isinstance(payload, dict):
        raise SourceError('공공데이터 응답 형식을 확인해 주세요.')
    if body_name:
        root = payload.get(body_name) or payload.get('response') or {}
        if not isinstance(root, dict):
            raise SourceError('공공데이터 응답 형식을 확인해 주세요.')
        header, body = root.get('header', root), root.get('body', root)
    else:
        root = payload.get('response', {})
        if not isinstance(root, dict):
            raise SourceError('공공데이터 응답 형식을 확인해 주세요.')
        header, body = root.get('header', {}), root.get('body', {})
    if not isinstance(header, dict) or not isinstance(body, dict):
        raise SourceError('공공데이터 응답 형식을 확인해 주세요.')
    if str(header.get('resultCode', '')).strip() not in ('00', '0000', '0'):
        raise api_error(stage, header.get('resultCode'))
    items = body.get('items') or {}
    if isinstance(items, dict):
        items = items.get('item') or []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise SourceError('공공데이터 응답 형식을 확인해 주세요.')
    try:
        total = int(body.get('totalCount', len(items)))
    except (ValueError, TypeError):
        raise SourceError('공공데이터 건수를 확인해 주세요.') from None
    return items, total


class OperatingService:
    def __init__(self, directory, get=requests.get):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db_path = self.directory / 'place_operating_info.sqlite3'
        self.get = get
        self.network_lock = threading.BoundedSemaphore(2)
        self.parking_lock = threading.Lock()
        self.stripes = [threading.Lock() for _ in range(32)]
        with closing(self.connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS manual (id TEXT PRIMARY KEY, value TEXT, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS public_cache (id TEXT PRIMARY KEY, signature TEXT, value TEXT, retry REAL, message TEXT);
                CREATE TABLE IF NOT EXISTS provider_cache (id TEXT PRIMARY KEY, value TEXT, fetched REAL);
            ''')

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def settings(self):
        return {
            'tour_configured': bool(os.getenv('TOUR_API_KEY', '').strip()),
            'parking_configured': bool(os.getenv('BUSAN_PARKING_API_KEY', '').strip()),
            'editing_configured': bool(os.getenv('OPERATING_INFO_EDIT_TOKEN', '').strip()),
        }

    def authorize(self, token):
        configured = os.getenv('OPERATING_INFO_EDIT_TOKEN', '').strip()
        if not configured:
            raise HTTPException(503, '서버에 운영정보 편집키가 설정되지 않았습니다.')
        if not token or not hmac.compare_digest(token.encode(), configured.encode()):
            raise HTTPException(403, '운영정보 편집키를 확인해 주세요.')

    def manual(self, key):
        with closing(self.connect()) as db:
            row = db.execute('SELECT * FROM manual WHERE id=?', (key,)).fetchone()
        if not row:
            return None, 0
        return json.loads(row['value']) if row['value'] else None, row['revision']

    def save(self, key, value, expected_revision):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM manual WHERE id=?', (key,)).fetchone()
            revision = row['revision'] if row else 0
            if expected_revision != revision:
                raise HTTPException(409, '다른 사용자가 먼저 수정했습니다. 최신 정보를 불러온 뒤 다시 저장해 주세요.')
            revision += 1
            db.execute('INSERT OR REPLACE INTO manual VALUES (?,?,?)', (key, json.dumps(value, ensure_ascii=False) if value else None, revision))
        return revision

    def request(self, url, params):
        # Fixed upstream endpoints; API keys never appear in returned errors/log messages.
        stage = '관광정보 위치검색' if url.endswith('/locationBasedList2') else '관광정보 상세조회' if url.endswith('/detailIntro2') else '공영주차장 조회'
        try:
            with self.network_lock:
                response = self.get(url, params=params, timeout=(5, 12))
                if response.status_code != 200:
                    raise SourceError(f'{stage}: HTTP {int(response.status_code)} 응답입니다.')
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError:
                    try:
                        root = ET.fromstring(response.content)
                        code = root.findtext('.//returnReasonCode') or root.findtext('.//resultCode')
                        if code:
                            raise api_error(stage, code)
                    except ET.ParseError:
                        pass
                    raise SourceError(f'{stage}: JSON 응답이 아닙니다.') from None
        except requests.Timeout:
            raise SourceError(f'{stage}: 응답 시간이 초과되었습니다.') from None
        except requests.RequestException:
            raise SourceError(f'{stage}: 통신에 실패했습니다. 기존 자료가 있으면 유지합니다.') from None

    def tour(self, place):
        key = os.getenv('TOUR_API_KEY', '').strip()
        if not key:
            return None, '관광정보 자동 조회는 TOUR_API_KEY 설정 후 사용할 수 있습니다.'
        params = {'serviceKey': unquote(key), 'MobileOS': 'WEB', 'MobileApp': 'CineSpotBusan', '_type': 'json', 'numOfRows': 100, 'pageNo': 1}
        query = {**params, 'mapX': place.lng, 'mapY': place.lat, 'radius': 300, 'arrange': 'E'}
        if place.kind == 'food':
            query['contentTypeId'] = '39'
        rows = []
        total = 1
        for page in range(1, 6):
            items, total = upstream_items(self.request(TOUR_BASE + 'locationBasedList2', {**query, 'pageNo': page}), stage='관광정보 위치검색')
            rows.extend(items)
            if len(rows) >= total:
                break
        if len(rows) < total:
            return None, '검색 후보가 많아 장소를 확정하지 못했습니다.'
        allowed = {'39'} if place.kind == 'food' else {'12', '14', '28', '38'}
        normalized = [dict(id=r.get('contentid'), name=r.get('title'), address=r.get('addr1'), lat=r.get('mapy'), lng=r.get('mapx'), original=r) for r in rows if str(r.get('contenttypeid')) in allowed and r.get('contentid')]
        match = unique_match(place, normalized)
        if not match:
            return None, '한국관광공사 자료에서 이름·위치가 일치하는 장소를 확정하지 못했습니다.'
        original = match['original']
        detail, _ = upstream_items(self.request(TOUR_BASE + 'detailIntro2', {**params, 'contentId': match['id'], 'contentTypeId': original['contenttypeid']}), stage='관광정보 상세조회')
        detail = [r for r in detail if str(r.get('contentid')) == str(match['id'])]
        if len(detail) != 1:
            raise SourceError('관광정보 상세 자료를 확인하지 못했습니다.')
        detail = detail[0]
        fields = {'39': ('opentimefood', 'restdatefood'), '12': ('usetime', 'restdate'), '14': ('usetimeculture', 'restdateculture'), '28': ('usetimeleports', 'restdateleports'), '38': ('opentime', 'restdateshopping')}
        hours_field, rest_field = fields[str(original['contenttypeid'])]
        hours, rest = plain(detail.get(hours_field)), plain(detail.get(rest_field))
        if not hours and not rest:
            return None, '일치하는 관광정보에 운영시간·휴무일이 등록되어 있지 않습니다.'
        info = dict(hours=hours[:2000], closedDays=rest[:500], status='unknown', source='한국관광공사 국문 관광정보 · ' + TOUR_SOURCE, checkedOn=today(), sourceUpdatedOn=self.source_date(original.get('modifiedtime')), matchedName=plain(original.get('title')), origin='public')
        return info, '공개자료는 실시간 영업·폐업 여부를 보증하지 않습니다.'

    @staticmethod
    def source_date(value):
        text = str(value or '').strip()
        for form, candidate in (('%Y%m%d', text[:8]), ('%Y-%m-%d', text[:10]), ('%m/%d/%Y', text[:10])):
            try:
                return datetime.strptime(candidate, form).date().isoformat()
            except ValueError:
                pass
        return ''

    def parking_rows(self):
        with self.parking_lock:
            with closing(self.connect()) as db:
                cached = db.execute("SELECT * FROM provider_cache WHERE id='parking'").fetchone()
            if cached and time.time() - cached['fetched'] < 86400:
                return json.loads(cached['value'])
            key = os.getenv('BUSAN_PARKING_API_KEY', '').strip()
            all_rows = []
            for page in range(1, 21):
                payload = self.request(PARKING_URL, {'ServiceKey': unquote(key), 'pageNo': page, 'numOfRows': 100, 'resultType': 'json'})
                # The Busan service uses a service-name root, not TourAPI's response wrapper.
                rows, total = upstream_items(payload, 'getPblcPrkngInfo', stage='공영주차장 조회')
                all_rows.extend(rows)
                if len(all_rows) >= total:
                    break
            if len(all_rows) < total:
                raise SourceError('주차장 전체 목록을 확인하지 못했습니다.')
            with closing(self.connect()) as db, db:
                db.execute('INSERT OR REPLACE INTO provider_cache VALUES (?,?,?)', ('parking', json.dumps(all_rows, ensure_ascii=False), time.time()))
            return all_rows

    def parking(self, place):
        if not os.getenv('BUSAN_PARKING_API_KEY', '').strip():
            return None, '주차장 자동 조회는 BUSAN_PARKING_API_KEY 설정 후 사용할 수 있습니다.'
        rows = self.parking_rows()
        normalized = [dict(id=r.get('mgntNum'), name=r.get('pkNam'), address=r.get('doroAddr') or r.get('jibunAddr'), lat=r.get('xCdnt'), lng=r.get('yCdnt'), original=r) for r in rows if r.get('mgntNum')]
        match = unique_match(place, normalized)
        if not match:
            return None, '공영주차장 자료에서 이름·위치가 일치하는 장소를 확정하지 못했습니다.'
        row = match['original']
        hours = []
        for title, start, end in [('평일', 'svcSrtTe', 'svcEndTe'), ('토요일', 'satSrtTe', 'satEndTe'), ('공휴일', 'hldSrtTe', 'hldEndTe')]:
            begin, finish = plain(row.get(start)), plain(row.get(end))
            if begin and finish:
                suffix = ' (동일 시각 표기: 24시간·미운영 여부 별도 확인)' if begin == finish else ''
                hours.append(f'{title} {begin}–{finish}{suffix}')
        if not hours:
            return None, '일치하는 공영주차장에 운영시간이 등록되어 있지 않습니다.'
        return dict(hours='\n'.join(hours), closedDays='', status='unknown', source='부산광역시 공영주차장 정보 · ' + PARKING_SOURCE, checkedOn=today(), sourceUpdatedOn=self.source_date(row.get('fnlDt')), matchedName=plain(row.get('pkNam')), origin='public', note='운영요일: ' + (plain(row.get('oprDay')) or '확인 필요')), '운영요일은 휴무일·무료개방 여부를 확정하는 정보가 아닙니다.'

    def lookup(self, place):
        index = int(hashlib.sha256(place.id.encode()).hexdigest()[:8], 16) % len(self.stripes)
        with self.stripes[index]:
            return self._lookup(place)

    def _lookup(self, place):
        manual, revision = self.manual(place.id)
        if manual:
            return dict(info={**manual, 'origin': 'manual'}, revision=revision, settings=self.settings(), message='서버에 저장된 직접 입력 정보입니다.', stale=False)
        settings_key = os.getenv('BUSAN_PARKING_API_KEY' if place.kind == 'parking' else 'TOUR_API_KEY', '').strip()
        signature = hashlib.sha256((place.model_dump_json() + settings_key).encode()).hexdigest()
        with closing(self.connect()) as db:
            cached = db.execute('SELECT * FROM public_cache WHERE id=? AND signature=?', (place.id, signature)).fetchone()
        value = json.loads(cached['value']) if cached and cached['value'] else None
        message = cached['message'] if cached else ''
        stale = False
        # Refresh legacy failed lookups immediately, preserving any retained data.
        legacy_error = bool(cached and '공공데이터 API 인증·응답을 확인해 주세요.' in message)
        if not cached or legacy_error or time.time() >= cached['retry']:
            try:
                value, message = self.parking(place) if place.kind == 'parking' else self.tour(place)
                retry = time.time() + (86400 if value else 3600)
            except SourceError as error:
                message = str(error)
                stale = bool(value)
                retry = time.time() + 300
            with closing(self.connect()) as db, db:
                stored_message = ('이전 자료 유지 · ' if stale else '') + message
                db.execute('INSERT OR REPLACE INTO public_cache VALUES (?,?,?,?,?)', (place.id, signature, json.dumps(value, ensure_ascii=False) if value else None, retry, stored_message))
                message = stored_message
        manual, revision = self.manual(place.id)
        if manual:
            return dict(info={**manual, 'origin': 'manual'}, revision=revision, settings=self.settings(), message='서버에 저장된 직접 입력 정보입니다.', stale=False)
        return dict(info=value, revision=revision, settings=self.settings(), message=message, stale=stale or message.startswith('이전 자료 유지 · '))


def register_operating_info(app, directory):
    service = OperatingService(directory)
    router = APIRouter(prefix='/api/place-operating-info', tags=['place operating information'])

    @router.get('/status')
    def status():
        return service.settings()

    @router.post('/lookup')
    def lookup(place: Place):
        return service.lookup(place)

    @router.post('/save')
    def save(body: SaveRequest, x_operating_edit_key: str = Header(default='')):
        service.authorize(x_operating_edit_key)
        value = body.info.model_dump(mode='json')
        revision = service.save(body.place_id, value, body.expected_revision)
        return {'info': {**value, 'origin': 'manual'}, 'revision': revision}

    @router.post('/remove')
    def remove(body: RemoveRequest, x_operating_edit_key: str = Header(default='')):
        service.authorize(x_operating_edit_key)
        revision = service.save(body.place_id, None, body.expected_revision)
        return {'revision': revision}

    app.include_router(router)
    return service
