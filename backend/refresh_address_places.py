"""Query user-confirmed names and addresses even if absent from the Kakao catalog."""
import argparse
import hashlib
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from naver_hours_collector import collect,norm,address_key,allowed
from naver_hours_service import NaverHours
from naver_place_matching import compatible_name
from refresh_confirmed_places import reset_idle_lock,lookup_name_matches
from repair_top3_operating_info import save_verified,usable

CONFIRMED=[
    ('섬들애복국 센텀본점','부산 해운대구 센텀중앙로 48',['섬들애복국']),
    ('다와푸드 센텀점','부산 해운대구 센텀중앙로 48',['다와푸드']),
    ('봄봄키친','부산 강서구 명지국제2로 80',[]),
    ('본도시락 부산명지점','부산 강서구 명지국제2로 80',['본도시락 명지점']),
    ('OK도매횟집 정관점','부산 기장군 정관읍 정관8로 25',['오케이도매횟집 정관점']),
]

def save_aliases(service,name,address,aliases,data):
    if not usable(data):raise ValueError('표시할 시간표가 없어 기존 자료를 유지합니다.')
    if not compatible_name(name,data.get('source_title','')):raise ValueError('확인된 상세 상호가 달라 저장하지 않았습니다.')
    names={norm(value):value for value in [name,*aliases]}
    for alias in names.values():
        if not compatible_name(alias,data.get('source_title','')):continue
        with closing(service.db()) as db:
            rows=db.execute('SELECT id,name,address,url FROM sources').fetchall()
        matches=[dict(row) for row in rows if norm(row['name'])==norm(alias) and address_key(row['address'])==address_key(address)]
        if not matches:
            key=hashlib.sha256((norm(alias)+'|'+address_key(address)).encode()).hexdigest()[:24]
            matches=[{'id':'confirmed-address:'+key,'name':alias,'address':address,'url':''}]
        for source in matches:save_verified(service,source,data)

def run(directory,reset_lock=False,collector=collect,only=None,url=''):
    selected=[item for item in CONFIRMED if not only or lookup_name_matches(item[0],only)]
    if not selected:raise ValueError('지정한 상호가 주소 확인 목록에 없습니다.')
    if url and (not only or len(selected)!=1 or not allowed(url)):
        raise ValueError('--url은 --only로 한 곳을 선택하고 HTTPS 네이버 주소를 지정해야 합니다.')
    service=NaverHours(directory)
    if reset_lock:reset_idle_lock(service)
    if url:
        diagnostic=Path.home()/'Downloads'/datetime.now().strftime('CineSpot_direct_result_%Y%m%d_%H%M%S')
        os.environ['NAVER_DEBUG_DIR']=str(diagnostic)
        print('확인 기록 폴더: '+str(diagnostic),flush=True)
    print(f'주소 확인된 {len(selected)}곳 조회 · 기존 주변 목록 등록 여부와 무관하게 실행',flush=True)
    for name,address,aliases in selected:
        with closing(service.db()) as db,db:
            claimed=db.execute('UPDATE lease SET until_time=? WHERE id=1 AND until_time<?',(time.time()+300,time.time())).rowcount
        if not claimed:raise ValueError('다른 작업의 조회 잠금이 있습니다. 다른 백엔드·수집을 종료한 뒤 --reset-lock로 실행해 주세요.')
        try:
            print(name+' / '+address,flush=True)
            options={'headless':os.getenv('NAVER_HEADLESS','true').lower()=='true'}
            if url:options['direct_only']=True
            data=collector(url,name,address,**options)
            save_aliases(service,name,address,aliases,data)
            print('  저장 완료 · '+data.get('source_title',''),flush=True)
            for row in data.get('weekly_hours',[]):
                print('  '+row['day']+'요일 '+('휴무' if row.get('closed') else ', '.join(row.get('hours',[])) or '확인 필요'),flush=True)
            print('  출처: '+data.get('source_url',''),flush=True)
        except Exception as error:
            print('  재조회 미완료: '+str(error)+' · 기존 자료 유지',flush=True)
            if '[BLOCKED]' in str(error):break
        finally:
            with closing(service.db()) as db,db:db.execute('UPDATE lease SET until_time=0 WHERE id=1')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--reset-lock',action='store_true')
    parser.add_argument('--only',help='주소 확인 목록에서 한 상호만 선택')
    parser.add_argument('--url',default='',help='사용자가 확인한 HTTPS 네이버 공유 링크로 직접 조회')
    args=parser.parse_args();root=Path(__file__).resolve().parent;load_dotenv(root/'.env')
    try:run(root/'data',args.reset_lock,only=args.only,url=args.url)
    except (ValueError,OSError,sqlite3.Error) as error:print('중단: '+str(error));raise SystemExit(1)
