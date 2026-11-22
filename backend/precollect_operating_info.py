"""Checkpointed precollection of every filming location's existing nearby-card candidates."""
import argparse
import hashlib
import json
import signal
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit

import requests
from naver_hours_service import NaverHours, Place

DIRECTORY = Path(__file__).resolve().parent / 'data'

class Deferred:
    def __init__(self): self.calls = []
    def add_task(self, function, *args): self.calls.append((function, args))

class Catalog:
    def __init__(self, directory):
        self.path = Path(directory) / 'operating_precollect.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.db()) as db, db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS anchors(id TEXT PRIMARY KEY, data TEXT, done INTEGER DEFAULT 0, error TEXT DEFAULT '', updated REAL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS places(id TEXT PRIMARY KEY, data TEXT);
            ''')

    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def add_anchors(self, locations):
        with closing(self.db()) as db, db:
            for location in locations:
                identity = {key: location.get(key) for key in ['name', 'address', 'lat', 'lng', 'kakaoKeyword']}
                fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO anchors(id,data) VALUES(?,?)', (fingerprint, json.dumps(location, ensure_ascii=False)))

    def pending(self):
        with closing(self.db()) as db:
            return [(row['id'], json.loads(row['data'])) for row in db.execute('SELECT * FROM anchors WHERE done=0 ORDER BY id')]

    def save(self, key, result):
        # One transaction: an interrupted anchor cannot lose its collected places.
        valid = [Place(**place).model_dump(exclude={'force_refresh'}) for place in result['places']]
        with closing(self.db()) as db, db:
            for place in valid:
                db.execute('INSERT OR REPLACE INTO places VALUES(?,?)', (place['id'], json.dumps(place, ensure_ascii=False)))
            note = '추정 위치 사용' if result.get('position', {}).get('estimated') else ''
            db.execute('UPDATE anchors SET done=1,error=?,updated=? WHERE id=?', (note, time.time(), key))

    def fail(self, key, message):
        with closing(self.db()) as db, db:
            db.execute('UPDATE anchors SET error=?,updated=? WHERE id=?', (message, time.time(), key))

    def places(self):
        with closing(self.db()) as db:
            return [Place(**json.loads(row[0])) for row in db.execute('SELECT data FROM places ORDER BY id')]

    def status(self):
        with closing(self.db()) as db:
            total, done = db.execute('SELECT COUNT(*),COALESCE(SUM(done),0) FROM anchors').fetchone()
            places = db.execute('SELECT COUNT(*) FROM places').fetchone()[0]
        print(f'촬영지: {done}/{total}곳 주변 검색 완료 · 중복 제거 장소: {places}곳', flush=True)

def local_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'http' or parsed.hostname not in {'localhost','127.0.0.1'} or parsed.username or parsed.password:
        raise argparse.ArgumentTypeError('로컬 서버 HTTP 주소를 사용해 주세요.')
    return value.rstrip('/')

def discover(catalog, backend, frontend, stop, refresh=False):
    try:
        response = requests.get(backend + '/api/all-filming-locations', timeout=(10,150))
        response.raise_for_status()
        data = response.json()
        locations = data.get('locations')
        if not isinstance(locations, list) or not locations:
            raise ValueError('전체 촬영지 목록이 비어 있습니다.')
        catalog.add_anchors(locations)
        if data.get('source_errors'):
            print('주의: 일부 촬영지 원본 API 오류가 있어 전체 목록이 아닐 수 있습니다.', flush=True)
    except (requests.RequestException, ValueError):
        with closing(catalog.db()) as db:
            exists = db.execute('SELECT COUNT(*) FROM anchors').fetchone()[0]
        if not exists:
            raise ValueError('전체 촬영지 API 조회에 실패했습니다. 백엔드와 원본 API를 확인해 주세요.') from None
        print('촬영지 API 조회 실패: 이전에 저장한 촬영지 목록으로 이어갑니다.', flush=True)
    if refresh:
        with closing(catalog.db()) as db, db:
            db.execute('UPDATE anchors SET done=0')
    pending = catalog.pending()
    if not pending or stop.is_set(): return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as driver:
        browser = driver.chromium.launch(channel='msedge', headless=True)
        try:
            page = browser.new_page()
            page.goto(frontend + '/cinespot-precollect.html', wait_until='domcontentloaded', timeout=30000)
            page.wait_for_function('Boolean(window.cinespotPrecollect)', timeout=30000)
            page.evaluate('() => window.cinespotPrecollect.prepare()')
            for index, (key, location) in enumerate(pending, 1):
                if stop.is_set(): break
                print(f'[주변 장소 {index}/{len(pending)}] {location.get("name", "촬영지")}', flush=True)
                try:
                    result = page.evaluate('(location) => window.cinespotPrecollect.discover(location)', location)
                    catalog.save(key, result)
                except Exception:
                    catalog.fail(key, '좌표·카카오 장소 검색 실패: 다음 실행에서 재시도')
                    print('  검색 실패: 진행 상황을 보존하고 다음 촬영지로 이동합니다.', flush=True)
                time.sleep(.3)
        finally:
            browser.close()

def collect_known(catalog, directory, stop):
    service = NaverHours(directory)
    deferred = Deferred()
    places = catalog.places()
    waiting = 0
    for place in places:
        if stop.is_set(): break
        result = service.lookup(place, deferred)
        waiting += bool(result['pending'])
    print(f'운영정보: 등록 {len(places)}곳 · 갱신 대기 {waiting}곳 (기존 최신 자료 재사용)', flush=True)
    # lookup obtained the shared lease; execute its deferred task once.
    if deferred.calls and not stop.is_set():
        deferred.calls[0][0](*deferred.calls[0][1])
    elif deferred.calls:
        with closing(service.db()) as db, db:
            db.execute('UPDATE lease SET until_time=0 WHERE id=1')
    completed = 0
    last_wait = 0
    while not stop.is_set():
        with closing(service.db()) as db, db:
            remaining = db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
            if not remaining: break
            claimed = db.execute('UPDATE lease SET until_time=? WHERE id=1 AND until_time<?', (time.time()+300, time.time())).rowcount
        if not claimed:
            if time.time()-last_wait >= 30:
                print(f'다른 수집 작업 완료 대기 · 남은 작업 {remaining}곳', flush=True)
                last_wait=time.time()
            time.sleep(1)
            continue
        service.run_next()
        completed += 1
        print(f'[운영정보 처리 {completed}] 남은 작업 약 {remaining-1}곳', flush=True)
    with closing(service.db()) as db:
        success, failed = db.execute("SELECT COALESCE(SUM(data IS NOT NULL),0),COALESCE(SUM(error != ''),0) FROM cache").fetchone()
    print(f'공유 캐시: 저장 자료 {success}곳 · 조회 오류 기록 {failed}곳', flush=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--backend',type=local_url,default='http://127.0.0.1:8000')
    parser.add_argument('--frontend',type=local_url,default='http://127.0.0.1:5173')
    parser.add_argument('--status',action='store_true')
    parser.add_argument('--refresh-nearby',action='store_true')
    parser.add_argument('--hours-only',action='store_true')
    args=parser.parse_args()
    # Read the backend's env explicitly; terminal auto-injection is not required.
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / '.env')
    catalog=Catalog(DIRECTORY)
    if args.status:
        catalog.status(); return
    stop=Event()
    def request_stop(*_):
        stop.set()
        print('중지 요청: 현재 장소 처리 후 저장하고 종료합니다.', flush=True)
    signal.signal(signal.SIGINT,request_stop)
    print('1단계: 전체 촬영지의 주변 카드 후보 검색 → 2단계: 중복 제거 후 운영정보 수집', flush=True)
    if not args.hours_only:
        discover(catalog,args.backend,args.frontend,stop,args.refresh_nearby)
    catalog.status()
    if not stop.is_set(): collect_known(catalog,DIRECTORY,stop)
    catalog.status()
    print('중지됨: 같은 명령으로 이어서 실행할 수 있습니다.' if stop.is_set() else '이번 수집 회차 종료. 미확인·실패 장소가 모두 해결됐다는 뜻은 아닙니다.',flush=True)

if __name__=='__main__':
    try: main()
    except Exception:
        print('수집 중단: 서버 주소·카카오 키·Edge/Playwright 설치 상태를 확인해 주세요. 저장된 결과는 유지됩니다.',file=sys.stderr)
        raise SystemExit(1)
