"""Persistent plain-text operating cards, with one queued browser refresh at a time."""
import hmac
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from naver_hours_schedule import estimate

REFRESH_SECONDS = 86400
FAILURE_SECONDS = 21600

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, Field
from naver_hours_collector import allowed, collect, norm, address_key


class Place(BaseModel):
    id: str = Field(pattern=r'^[0-9]{1,30}$')
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=500)
    phone: str = Field(default='', max_length=60)
    force_refresh: bool = False
    kind: str = Field(default='', max_length=20)


class Link(Place):
    url: str = Field(min_length=1, max_length=500)


def epoch(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.timestamp() if parsed.tzinfo else 0
    except (ValueError, TypeError):
        return 0


class NaverHours:
    def __init__(self, directory, collector=collect):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'naver_operating_info.sqlite3'
        self.collector = collector
        with closing(self.db()) as db, db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, name TEXT, address TEXT, url TEXT);
              CREATE TABLE IF NOT EXISTS cache (id TEXT PRIMARY KEY, data TEXT, error TEXT, retry REAL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS lease (id INTEGER PRIMARY KEY CHECK(id=1), until_time REAL);
              INSERT OR IGNORE INTO lease VALUES(1,0);
              CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request TEXT, created REAL, running REAL DEFAULT 0);
            ''')
            db.execute('INSERT OR IGNORE INTO sources VALUES (?,?,?,?)', ('verified-todam', '토담밥집', '부산 영도구 하나길 466', 'https://map.naver.com/p/entry/place/1755049474'))
            seed = directory / 'naver_hours_seed.json'
            if seed.is_file():
                try:
                    data = json.loads(seed.read_text(encoding='utf-8-sig'))
                    if data.get('source_url') == 'https://map.naver.com/p/entry/place/1755049474' and data.get('complete_week'):
                        data.update(source_title='토담밥집', source_address='부산 영도구 하나길 466')
                        db.execute('INSERT OR IGNORE INTO cache VALUES (?,?,?,?)', ('verified-todam', json.dumps(data, ensure_ascii=False), '', 0))
                except (ValueError, OSError):
                    pass

        # Import reviewed public operating information shared with the team.
        from operating_info_exchange import import_shared_operating_info
        import_shared_operating_info(directory)

    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def find_source(self, place):
        with closing(self.db()) as db:
            exact = db.execute('SELECT * FROM sources WHERE id=?', (place.id,)).fetchone()
            if exact and norm(exact['name']) == norm(place.name) and address_key(exact['address']) == address_key(place.address):
                return dict(exact)
            rows = db.execute('SELECT * FROM sources').fetchall()
        matches = [dict(row) for row in rows if norm(row['name']) == norm(place.name) and address_key(row['address']) == address_key(place.address)]
        return matches[0] if len(matches) == 1 else None

    def lookup(self, place, background):
        source = self.find_source(place)
        if not source:
            identity = norm(place.name) + '|' + address_key(place.address)
            auto_id = 'auto:' + place.id + ':' + hashlib.sha256(identity.encode()).hexdigest()[:12]
            source = {'id': auto_id, 'name': place.name, 'address': place.address, 'url': ''}
        source['phone'] = place.phone
        source['kind'] = place.kind
        with closing(self.db()) as db, db:
            row = db.execute('SELECT * FROM cache WHERE id=?', (source['id'],)).fetchone()
            data = json.loads(row['data']) if row and row['data'] else None
            error = row['error'] if row else ''
            age = max(0, time.time() - epoch(data.get('checked_at'))) if data else float('inf')
            pending = False
            retry_ready = not row or row['retry'] <= time.time()
            forced = place.force_refresh and age >= 300 and (not error or row['retry'] - FAILURE_SECONDS + 300 <= time.time())
            parking = place.kind == 'parking' or (not place.kind and '주차' in place.name)
            if not parking and ((age >= REFRESH_SECONDS and retry_ready) or forced):
                db.execute('INSERT INTO jobs(id,request,created,running) VALUES(?,?,?,0) ON CONFLICT(id) DO UPDATE SET request=excluded.request', (source['id'], json.dumps(source, ensure_ascii=False), time.time()))
                pending = True
                changed = db.execute('UPDATE lease SET until_time=? WHERE id=1 AND until_time<?', (time.time() + 300, time.time())).rowcount
                if changed:
                    background.add_task(self.run_next)
            # A stale observed status must never be labeled as the current status.
            if data:
                data = {**data, 'status_fresh': age < 300 and not error, 'estimated': estimate(data)}
        return {'data': data, 'pending': pending, 'message': error}

    def run_next(self):
        with closing(self.db()) as db, db:
            job = db.execute('SELECT * FROM jobs WHERE running<? ORDER BY created,id LIMIT 1', (time.time(),)).fetchone()
            if job:
                db.execute('UPDATE jobs SET running=? WHERE id=?', (time.time() + 300, job['id']))
            else:
                db.execute('UPDATE lease SET until_time=0 WHERE id=1')
                return
        source = json.loads(job['request'])
        if source.get('kind') == 'parking' or (not source.get('kind') and '주차' in source.get('name', '')):
            with closing(self.db()) as db,db:
                db.execute('DELETE FROM jobs WHERE id=?',(source['id'],))
                db.execute('UPDATE lease SET until_time=0 WHERE id=1')
            return
        self.refresh(source)

    def refresh(self, source):
        try:
            data = self.collector(source['url'], source['name'], source['address'], headless=os.getenv('NAVER_HEADLESS', 'true').lower() == 'true', phone=source.get('phone', ''))
            if not data.get('weekly_hours') and data.get('status', 'unknown') == 'unknown':
                raise ValueError('운영시간을 읽지 못했습니다.')
            if not allowed(data.get('source_url', '')):
                raise ValueError('확인된 네이버 출처 주소가 없습니다.')
            with closing(self.db()) as db, db:
                if not source['url']:
                    db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?)', (source['id'], source['name'], source['address'], data['source_url']))
                db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?,?)', (source['id'], json.dumps(data, ensure_ascii=False), '', time.time() + 300))
        except ValueError as error:
            with closing(self.db()) as db, db:
                db.execute('INSERT INTO cache VALUES(?,NULL,?,?) ON CONFLICT(id) DO UPDATE SET error=excluded.error,retry=excluded.retry', (source['id'], str(error)[:300], time.time() + FAILURE_SECONDS))
        except Exception:
            with closing(self.db()) as db, db:
                db.execute('INSERT INTO cache VALUES(?,NULL,?,?) ON CONFLICT(id) DO UPDATE SET error=excluded.error,retry=excluded.retry', (source['id'], '수집 중 오류가 발생했습니다. 이전 시간표가 있으면 유지합니다.', time.time() + FAILURE_SECONDS))
        finally:
            with closing(self.db()) as db, db:
                db.execute('DELETE FROM jobs WHERE id=?', (source['id'],))
                db.execute('UPDATE lease SET until_time=0 WHERE id=1')

    def link(self, place, token):
        expected = os.getenv('OPERATING_INFO_EDIT_TOKEN', '').strip()
        if not expected or not hmac.compare_digest(expected.encode(), token.encode()):
            raise HTTPException(403, '기존 운영정보 편집키를 확인해 주세요.')
        if not allowed(place.url):
            raise HTTPException(400, 'HTTPS 네이버 장소 주소를 입력해 주세요.')
        with closing(self.db()) as db, db:
            old = db.execute('SELECT * FROM sources WHERE id=?', (place.id,)).fetchone()
            unchanged = old and old['url'] == place.url and old['name'] == place.name and old['address'] == place.address
            db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?)', (place.id, place.name, place.address, place.url))
            if not unchanged:
                db.execute('DELETE FROM cache WHERE id=?', (place.id,))


def register_naver_hours(app, directory):
    service = NaverHours(directory)
    router = APIRouter(prefix='/api/naver-operating-info', tags=['Naver operating information'])

    @router.post('/lookup')
    def lookup(place: Place, background: BackgroundTasks):
        return service.lookup(place, background)

    @router.post('/link')
    def link(place: Link, x_operating_edit_key: str = Header(default='')):
        service.link(place, x_operating_edit_key)
        return {'ok': True}

    app.include_router(router)
    return service
