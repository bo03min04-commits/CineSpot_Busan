from nearby_place_rules import correct_places
"""Precollect only candidates for the same latest/popular 12 films shown by the UI."""
import argparse
import hashlib
import json
import re
import signal
import time
from contextlib import closing
from pathlib import Path
from threading import Event

import requests
from precollect_operating_info import Catalog, local_url
from naver_hours_service import NaverHours, FAILURE_SECONDS
from naver_hours_collector import norm, address_key

DIRECTORY=Path(__file__).resolve().parent/'data'

LATEST_THREE=['어쩔수가없다','천국은 없다','소주전쟁']

def selected_titles(backend, get=requests.get):
    response=get(backend+'/api/recommended-movies',params={'sort':'popular'},timeout=(10,150))
    response.raise_for_status()
    movies=response.json().get('movies')
    if not isinstance(movies,list): raise ValueError('인기순 영화 응답 형식을 확인해 주세요.')
    eligible=[m for m in movies if isinstance(m,dict) and m.get('title') and type(m.get('audience')) is int and m['audience']>0]
    top=eligible[:12]
    if not top: raise ValueError('인기순 목록이 비어 있습니다.')
    popular=[m['title'] for m in top]
    print('먼저 수집 · 인기순: '+', '.join(popular),flush=True)
    print('그다음 · 최신 지정 3편: '+', '.join(LATEST_THREE),flush=True)
    return list(dict.fromkeys(popular+LATEST_THREE))


def make_catalog(directory,titles):
    previous_signature=hashlib.sha256(json.dumps(sorted(titles),ensure_ascii=False).encode()).hexdigest()[:16]
    signature=hashlib.sha256(('food_spot_only_v1|'+json.dumps(sorted(titles),ensure_ascii=False)).encode()).hexdigest()[:16]
    catalog=Catalog(Path(directory)/('recommended_'+signature))
    with closing(catalog.db()) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,result TEXT,updated REAL)')
        db.execute('CREATE TABLE IF NOT EXISTS anchor_rank(id TEXT PRIMARY KEY,rank INTEGER)')
        db.execute('CREATE TABLE IF NOT EXISTS place_rank(id TEXT PRIMARY KEY,rank INTEGER)')
    previous=Path(directory)/('recommended_'+previous_signature)/'operating_precollect.sqlite3'
    if previous.is_file():
        import sqlite3
        with closing(sqlite3.connect(previous.resolve().as_uri()+'?mode=ro',uri=True)) as old:
            attempts=old.execute('SELECT id,result,updated FROM attempts').fetchall()
        with closing(catalog.db()) as db,db:
            db.executemany('INSERT OR IGNORE INTO attempts VALUES(?,?,?)',attempts)
    return catalog

def prepare_anchors(catalog,titles,backend,get=requests.get):
    # The UI uses this same endpoint, including its title matching behavior.
    failed=[]
    for rank,title in enumerate(titles):
        try:
            response=get(backend+'/api/filming-locations',params={'movie':title},timeout=(10,150))
            response.raise_for_status()
            locations=response.json().get('locations')
            if not isinstance(locations,list): raise ValueError('invalid locations')
            catalog.add_anchors(locations)
            with closing(catalog.db()) as db,db:
                for location in locations:
                    identity={key:location.get(key) for key in ['name','address','lat','lng','kakaoKeyword']}
                    fingerprint=hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                    db.execute('INSERT INTO anchor_rank VALUES(?,?) ON CONFLICT(id) DO UPDATE SET rank=MIN(rank,excluded.rank)',(fingerprint,rank))
            if not locations: failed.append(title)
        except (requests.RequestException,ValueError):
            failed.append(title)
    if failed:
        print('촬영지 조회 실패 또는 미등록: '+', '.join(failed),flush=True)
    return failed

def discover_selected(catalog,frontend,stop):
    pending=catalog.pending()
    with closing(catalog.db()) as db:
        ranks={row['id']:row['rank'] for row in db.execute('SELECT * FROM anchor_rank')}
    pending.sort(key=lambda item:(ranks.get(item[0],999),item[0]))
    if not pending: return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as driver:
        browser=driver.chromium.launch(channel='msedge',headless=True)
        try:
            page=browser.new_page()
            page.goto(frontend+'/cinespot-precollect.html',wait_until='domcontentloaded',timeout=30000)
            page.wait_for_function('Boolean(window.cinespotPrecollect)',timeout=30000)
            page.evaluate('() => window.cinespotPrecollect.prepare()')
            for index,(key,location) in enumerate(pending,1):
                if stop.is_set(): break
                print(f'[추천 영화 주변 검색 {index}/{len(pending)}] {location.get("name", "촬영지")}',flush=True)
                try:
                    result=page.evaluate('(location) => window.cinespotPrecollect.discover(location)',location)
                    catalog.save(key,result)
                    with closing(catalog.db()) as db,db:
                        for place in result['places']:
                            db.execute('INSERT INTO place_rank VALUES(?,?) ON CONFLICT(id) DO UPDATE SET rank=MIN(rank,excluded.rank)',(place['id'],ranks.get(key,999)))
                except Exception as error:
                    message=re.sub(r'(?i)(appkey|servicekey|api_key|key)=[^&\s]+',r'\1=[숨김]',str(error))[:300]
                    catalog.fail(key,'주변 검색 실패: '+message)
                    print(f'  주변 검색 실패 ({type(error).__name__}): {message}',flush=True)
                time.sleep(.3)
        finally: browser.close()

def source_for(service,place):
    source=service.find_source(place)
    if not source:
        identity=norm(place.name)+'|'+address_key(place.address)
        source={'id':'auto:'+place.id+':'+hashlib.sha256(identity.encode()).hexdigest()[:12],
                'name':place.name,'address':place.address,'url':''}
    return {**source,'phone':place.phone}

def collect_selected(catalog,directory,stop,service=None):
    service=service or NaverHours(directory)
    places=correct_places(catalog.places())
    with closing(catalog.db()) as db:
        ranks={row['id']:row['rank'] for row in db.execute('SELECT * FROM place_rank')}
    places.sort(key=lambda place:(ranks.get(place.id,999),place.id))
    counts={'reused':0,'saved':0,'failed':0,'previous':0,'cooldown':0}
    print(f'맛집·갈 만한 장소 중복 제거: {len(places)}곳 · 주차장 운영시간 수집 제외',flush=True)
    for index,place in enumerate(places,1):
        if stop.is_set(): break
        with closing(catalog.db()) as db:
            attempted=db.execute('SELECT result FROM attempts WHERE id=?',(place.id,)).fetchone()
        if attempted:
            counts['previous']+=1
            continue
        source=source_for(service,place)
        with closing(service.db()) as db:
            cached=db.execute('SELECT * FROM cache WHERE id=?',(source['id'],)).fetchone()
        # One-time collection: keep existing data even when older than 24 hours.
        if cached and cached['data']:
            outcome='reused'
        elif cached and cached['error'] and cached['retry'] - FAILURE_SECONDS + 300 > time.time():
            counts['cooldown']+=1
            continue
        else:
            announced=False
            claimed=0
            while not stop.is_set():
                with closing(service.db()) as db, db:
                    claimed=db.execute('UPDATE lease SET until_time=? WHERE id=1 AND until_time<?',
                                       (time.time()+300,time.time())).rowcount
                if claimed: break
                if not announced:
                    print('  현재 진행 중인 다른 조회가 끝나기를 기다립니다.',flush=True)
                    announced=True
                time.sleep(1)
            if stop.is_set():
                if claimed:
                    with closing(service.db()) as db, db: db.execute('UPDATE lease SET until_time=0 WHERE id=1')
                break
            started=time.monotonic()
            # Do NOT run service.run_next(): it would drain unrelated old bulk jobs.
            service.refresh(source)
            with closing(service.db()) as db:
                row=db.execute('SELECT data,error FROM cache WHERE id=?',(source['id'],)).fetchone()
            outcome='saved' if row and row['data'] and not row['error'] else 'failed'
            print(f'[{index}/{len(places)}] {place.name}: '+('저장' if outcome=='saved' else '확인 실패')+
                  f' · {time.monotonic()-started:.1f}초',flush=True)
            if outcome=='failed' and row and row['error']:
                print('  사유: '+row['error'],flush=True)
        with closing(catalog.db()) as db, db:
            db.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)',(place.id,outcome,time.time()))
        counts[outcome]+=1
    print('이번 실행: 기존 자료 재사용 {reused} · 새 저장 {saved} · 확인 실패 {failed} · 이전 처리 건너뜀 {previous} · 재시도 대기 {cooldown}'.format(**counts),flush=True)
    return counts

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--backend',type=local_url,default='http://127.0.0.1:8000')
    parser.add_argument('--frontend',type=local_url,default='http://127.0.0.1:5173')
    parser.add_argument('--retry-failed',action='store_true')
    args=parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent/'.env')
    stop=Event()
    def interrupt(*_):
        stop.set(); print('중지 요청: 현재 장소가 끝나면 저장하고 종료합니다.',flush=True)
    signal.signal(signal.SIGINT,interrupt)
    titles=selected_titles(args.backend)
    print(f'인기순 우선 + 최신 지정 3편 · 중복 제거 영화: {len(titles)}개',flush=True)
    print('주차장을 제외한 새 후보 목록을 만듭니다. 저장된 운영정보는 재사용합니다.',flush=True)
    catalog=make_catalog(DIRECTORY,titles)
    failed=prepare_anchors(catalog,titles,args.backend)
    if not catalog.pending() and not catalog.places():
        raise ValueError('선택한 영화의 촬영지·주변 장소를 확보하지 못했습니다.')
    if args.retry_failed:
        with closing(catalog.db()) as db, db: db.execute("DELETE FROM attempts WHERE result='failed'")
    if not stop.is_set(): discover_selected(catalog,args.frontend,stop)
    catalog.status()
    if not stop.is_set(): collect_selected(catalog,DIRECTORY,stop)
    print('중지됨: 같은 추천 목록이면 다음 실행에 이어갑니다.' if stop.is_set() else '추천 영화 우선 수집 회차 종료.',flush=True)
    if failed: print('일부 영화 촬영지 미확보: 위 목록을 확인해 주세요.',flush=True)

if __name__=='__main__':
    try: main()
    except Exception as error:
        print(f'중단 ({type(error).__name__}): 로컬 서버·API 응답·키 설정을 확인해 주세요. 저장 자료는 유지됩니다.',flush=True)
        if isinstance(error,ValueError): print(str(error),flush=True)
        raise SystemExit(1)
