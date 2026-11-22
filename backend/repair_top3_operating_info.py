"""Bounded repair of missing hours for popular ranks 1–3 using existing candidates."""
import argparse
import hashlib
import json
import os
import re
import signal
import sqlite3
import time
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path
from threading import Event
from urllib.parse import quote
from zipfile import ZipFile,ZIP_DEFLATED

import requests
from naver_hours_service import NaverHours,Place
from precollect_recommended_info import source_for,local_url
from naver_hours_collector import collect,allowed
from nearby_place_rules import correct_place

LATEST=['어쩔수가없다','천국은 없다','소주전쟁']
COLLECTOR_VERSION='identity_hours_v4_user_corrections'

def readonly(path):
    if not path.is_file():raise ValueError('기존 수집 목록이 없습니다: '+path.name)
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=15)
    db.row_factory=sqlite3.Row
    return db

def usable(data):
    if not isinstance(data,dict):return False
    rows=data.get('weekly_hours') or []
    return any(re.fullmatch(r'\d{2}:\d{2}–\d{2}:\d{2}',text) for row in rows if not row.get('closed') for text in row.get('hours',[]))

def targets(directory,backend,get=requests.get):
    response=get(backend+'/api/recommended-movies',params={'sort':'popular'},timeout=(10,90));response.raise_for_status()
    movies=response.json().get('movies',[])
    top=[m['title'] for m in movies if isinstance(m,dict) and m.get('title') and type(m.get('audience')) is int and m['audience']>0][:12]
    if len(top)<3:raise ValueError('인기순 3편을 확인하지 못했습니다.')
    titles=list(dict.fromkeys(top+LATEST))
    signature=hashlib.sha256(('food_spot_only_v1|'+json.dumps(sorted(titles),ensure_ascii=False)).encode()).hexdigest()[:16]
    path=directory/('recommended_'+signature)/'operating_precollect.sqlite3'
    with closing(readonly(path)) as db:
        selected=db.execute('SELECT p.data,r.rank FROM places p JOIN place_rank r ON p.id=r.id WHERE r.rank<3 ORDER BY r.rank,p.id').fetchall()
    if not selected:raise ValueError('인기순 1~3위의 기존 주변 장소 목록이 없습니다.')
    items=[]
    for row in selected:
        place=correct_place(Place(**json.loads(row['data'])))
        if place is not None:items.append({'place':place,'rank':row['rank']+1})
    return top[:3],items

def alternative_url(place,source):
    if source.get('url'):return source['url'],'확인된 업체 직접 조회'
    return '','이름·주소 우선 검색, 다른 검색어로 보충'

def save_verified(service,source,data):
    if not usable(data) or not allowed(data.get('source_url','')):
        raise ValueError('[NO_USABLE_HOURS] 읽은 자료에 표시 가능한 영업시간이 없습니다.')
    with closing(service.db()) as db,db:
        db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?)',(source['id'],source['name'],source['address'],data['source_url']))
        db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?,?)',(source['id'],json.dumps(data,ensure_ascii=False),'',time.time()+300))
        db.execute('DELETE FROM jobs WHERE id=?',(source['id'],))

def run(directory,backend,minutes,stop,collector=collect,get=requests.get,output=None):
    titles,items=targets(directory,backend,get)
    print('이번 대상: '+', '.join(f'{i+1}위 {title}' for i,title in enumerate(titles)),flush=True)
    service=NaverHours(directory)
    checkpoint=directory/'top3_repair_progress.sqlite3'
    with sqlite3.connect(checkpoint) as db:
        db.execute('CREATE TABLE IF NOT EXISTS checked(id TEXT PRIMARY KEY,result TEXT,time REAL)')
        columns={row[1] for row in db.execute('PRAGMA table_info(checked)')}
        if 'collector_version' not in columns:
            db.execute("ALTER TABLE checked ADD COLUMN collector_version TEXT DEFAULT ''")
    folder=output or Path.home()/'Downloads'/datetime.now().strftime('CineSpot_top3_result_%Y%m%d_%H%M%S_%f')
    folder.mkdir(parents=True,exist_ok=True)
    os.environ['NAVER_DEBUG_DIR']=str(folder/'traces')
    reasons=Counter();results=[];pending=[]
    for item in items:
        place=item['place'];source=source_for(service,place)
        with closing(service.db()) as db:row=db.execute('SELECT data,error FROM cache WHERE id=?',(source['id'],)).fetchone()
        old=json.loads(row['data']) if row and row['data'] else None
        if usable(old):reasons['기존 시간표 있음']+=1;continue
        error=row['error'] if row else ''
        if '[HOURS_NOT_LISTED]' in error:
            reasons['이전 조회에서 시간표 미확인']+=1
            results.append({'name':place.name,'rank':item['rank'],'result':'not_listed','previous_error':error});continue
        with sqlite3.connect(checkpoint) as db:checked=db.execute('SELECT result FROM checked WHERE id=? AND collector_version=?',(source['id'],COLLECTOR_VERSION)).fetchone()
        if checked:reasons['이번 보충 도구에서 이전 검사함']+=1;continue
        pending.append((item,source,error))
    print('전체 후보: '+str(len(items))+'곳 · '+str(dict(reasons))+' · 추가 확인 '+str(len(pending))+'곳',flush=True)
    deadline=time.monotonic()+minutes*60
    for item,source,error in pending:
        if stop.is_set() or time.monotonic()>=deadline:break
        place=item['place'];claimed=0
        while not stop.is_set() and time.monotonic()<deadline:
            with closing(service.db()) as db,db:
                claimed=db.execute('UPDATE lease SET until_time=? WHERE id=1 AND until_time<?',(time.time()+300,time.time())).rowcount
            if claimed:break
            time.sleep(1)
        if not claimed:break
        result={'name':place.name,'address':place.address,'rank':item['rank'],'previous_error':error}
        began=time.monotonic()
        try:
            url,method=alternative_url(place,source);result['method']=method
            print(f'[{item["rank"]}위 주변] {place.name} · {method}',flush=True)
            data=collector(url,place.name,place.address,phone=place.phone,headless=os.getenv('NAVER_HEADLESS','true').lower()=='true')
            save_verified(service,source,data)
            result.update(result='saved',data=data);reasons['새 시간표 저장']+=1
            print('  시간표 저장',flush=True)
        except Exception as exception:
            message=str(exception)[:1000]
            result.update(result='unresolved',error=message);reasons['추가 확인 실패']+=1
            print('  '+message,flush=True)
            if '[BLOCKED]' in message or '[BROWSER_SETUP]' in message:stop.set()
        finally:
            with closing(service.db()) as db,db:db.execute('UPDATE lease SET until_time=0 WHERE id=1')
        result['seconds']=round(time.monotonic()-began,2);results.append(result)
        with sqlite3.connect(checkpoint) as db:db.execute('INSERT OR REPLACE INTO checked(id,result,time,collector_version) VALUES(?,?,?,?)',(source['id'],result['result'],time.time(),COLLECTOR_VERSION))
    summary={'movies':titles,'counts':dict(reasons),'pending_unchecked':max(0,len(pending)-sum(r.get('result') in {'saved','unresolved'} for r in results)),'results':results}
    (folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    output_zip=folder.with_suffix('.zip')
    with ZipFile(output_zip,'w',ZIP_DEFLATED) as archive:
        for path in folder.rglob('*.json'):archive.write(path,path.relative_to(folder))
    print('이번 집계: '+str(dict(reasons)),flush=True)
    print('아직 검사하지 못한 장소: '+str(summary['pending_unchecked']),flush=True)
    print('결과 파일: '+str(output_zip),flush=True)
    return summary

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--minutes',type=int,default=20)
    parser.add_argument('--backend',type=local_url,default='http://127.0.0.1:8000');args=parser.parse_args()
    if not 1<=args.minutes<=120:raise ValueError('--minutes는 1~120 사이로 지정해 주세요.')
    backend=Path(__file__).resolve().parent
    from dotenv import load_dotenv
    load_dotenv(backend/'.env')
    stop=Event()
    def interrupt(*_):stop.set();print('현재 장소 처리 후 결과를 저장하고 종료합니다.',flush=True)
    signal.signal(signal.SIGINT,interrupt)
    run(backend/'data',args.backend,args.minutes,stop)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print(f'중단 ({type(error).__name__}): {error}',flush=True);raise SystemExit(1)
