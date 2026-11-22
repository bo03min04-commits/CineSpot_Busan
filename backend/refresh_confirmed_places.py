"""Recheck just the two reviewed venues; do not clear global failure records."""
import argparse
import json
import os
import re
import time
import sqlite3
import subprocess
from difflib import SequenceMatcher
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from naver_hours_collector import collect
from naver_hours_service import NaverHours,Place
from nearby_place_rules import street,compact,correct_place
from naver_place_matching import spoken_initials
from precollect_recommended_info import source_for,local_url
from repair_top3_operating_info import targets,save_verified

WANTED=[('대궐면옥 엔 한우암소갈비','부산 해운대구 센텀6로 21'),
        ('만리장성','부산 기장군 정관읍 가동길 8-9')]

def lookup_name_matches(actual,requested):
    a=compact(spoken_initials(actual));b=compact(spoken_initials(requested))
    if a==b:return True
    words=requested.split()
    if len(words)>1:
        brand=compact(spoken_initials(words[0]))
        if a.startswith(brand) and b.startswith(brand):
            return a[len(brand):].removeprefix('부산')==b[len(brand):].removeprefix('부산')
    return len(b)>=3 and a.startswith(b) and a.endswith('점')

def local_items(directory):
    items={}
    for path in sorted(Path(directory).rglob('operating_precollect.sqlite3'),key=lambda p:p.stat().st_mtime,reverse=True):
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'places' not in tables:continue
            for raw, in db.execute('SELECT data FROM places'):
                place=correct_place(Place(**json.loads(raw)))
                if place is not None:items.setdefault((place.id,street(place.address)),{'place':place})
    operating=Path(directory)/'naver_operating_info.sqlite3'
    if operating.is_file():
        with closing(sqlite3.connect(operating.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)) as db:
            for id,name,address in db.execute('SELECT id,name,address FROM sources'):
                match=re.match(r'^(?:auto:)?(\d{1,30})(?::|$)',id)
                if not match:continue
                place=correct_place(Place(id=match.group(1),name=name,address=address))
                if place is not None:items.setdefault((place.id,street(place.address)),{'place':place})
    return list(items.values())

def running_collectors():
    if os.name!='nt':raise ValueError('--reset-lock는 Windows에서 다른 수집 프로세스를 검사한 뒤에만 사용할 수 있습니다.')
    # Only return PIDs, not command lines which could contain private parameters.
    script=("@(Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -match '^python(?:w|3)?(?:\\.exe)?$' -and "
            f"$_.ProcessId -ne {os.getpid()} -and "
            "$_.CommandLine -match 'uvicorn|precollect|repair_top3|refresh_confirmed|naver_hours' "
            "} | Select-Object -ExpandProperty ProcessId) | ConvertTo-Json -Compress")
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=20)
    if result.returncode:raise ValueError('다른 수집 프로세스 확인에 실패해 잠금을 변경하지 않았습니다.')
    value=json.loads(result.stdout.strip() or '[]')
    return value if isinstance(value,list) else [value]

def reset_idle_lock(service):
    active=running_collectors()
    if active:raise ValueError('백엔드 또는 다른 수집 프로세스가 남아 있어 잠금을 해제하지 않았습니다. PID: '+', '.join(map(str,active)))
    with closing(service.db()) as db,db:db.execute('UPDATE lease SET until_time=0 WHERE id=1')
    print('실행 중인 다른 수집 프로세스 없음 · 이전 잠금 해제 완료',flush=True)

def selected_groups(items,names):
    groups={}
    if not names:
        wanted={(compact(n),street(a)) for n,a in WANTED}
        selected=[item['place'] for item in items if (compact(item['place'].name),street(item['place'].address)) in wanted]
    else:
        selected=[]
        for item in items:
            place=item['place']
            for name in names:
                if lookup_name_matches(place.name,name):
                    selected.append(place);break
    for place in selected:
        # Never mix the timetables of two branches, even if their names coincide.
        key=(compact(place.name),street(place.address))
        groups.setdefault(key,{})[place.id]=place
    return list(groups.values())

def run(directory,backend,names=None,reset_lock=False):
    if names:
        items=local_items(directory)
        print('저장된 주변 목록 전체 확인: '+str(len(items))+'곳',flush=True)
    else:
        _,items=targets(directory,backend)
    service=NaverHours(directory)
    groups=selected_groups(items,names)
    print('이번 재조회 대상: '+str(len(groups))+'곳 (주소별 구분)',flush=True)
    for requested in names or []:
        if not any(lookup_name_matches(item['place'].name,requested) for item in items):
            print('요청 상호 미발견: '+requested,flush=True)
            similar=sorted(items,key=lambda item:SequenceMatcher(None,compact(item['place'].name),compact(requested)).ratio(),reverse=True)[:5]
            for item in similar:
                place=item['place'];print('  비슷한 등록명: '+place.name+' / '+place.address,flush=True)
    if not groups:
        raise ValueError('저장된 주변 목록 전체에서 대상을 찾지 못했습니다. 위 등록명·주소를 확인해 주세요. 기존 자료는 유지됩니다.')
    if reset_lock:reset_idle_lock(service)
    for matches in groups:
        first=next(iter(matches.values()));name=first.name;address=first.address
        claimed=0
        for _ in range(90):
            with closing(service.db()) as db,db:
                claimed=db.execute('UPDATE lease SET until_time=? WHERE id=1 AND until_time<?',(time.time()+300,time.time())).rowcount
            if claimed:break
            time.sleep(1)
        if not claimed:raise ValueError('조회 잠금을 얻지 못했습니다. 다른 수집 또는 이전 작업의 잠금 기록이 남아 있을 수 있습니다.')
        try:
            print(f'{name}: 이름·주소로 새 운영시간 조회 중',flush=True)
            print('  주소: '+address,flush=True)
            # Ignore the old phone-only query, old source URL, retry cooldown and
            # old failure checkpoint. The collector verifies the detail identity.
            data=collect('',name,address,phone=first.phone,
                         headless=os.getenv('NAVER_HEADLESS','true').lower()=='true')
            for place in matches.values():save_verified(service,source_for(service,place),data)
            print('  저장 완료 · '+str(data.get('source_title','')),flush=True)
            for row in data.get('weekly_hours',[]):
                print('  '+row['day']+'요일 '+('휴무' if row.get('closed') else ', '.join(row.get('hours',[])) or '정보 없음'),flush=True)
            print('  출처: '+data.get('source_url',''),flush=True)
        except Exception as error:
            print(f'  재조회 미완료: {error} · 기존 시간표 유지',flush=True)
        finally:
            with closing(service.db()) as db,db:db.execute('UPDATE lease SET until_time=0 WHERE id=1')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--backend',type=local_url,default='http://127.0.0.1:8000')
    parser.add_argument('--name',action='append',help='재조회할 상호. 여러 번 지정 가능, 지점은 주소별로 구분합니다.')
    parser.add_argument('--reset-lock',action='store_true',help='모든 백엔드·수집 종료 후 남은 잠금만 해제. 다른 프로세스가 있으면 거절합니다.')
    args=parser.parse_args();root=Path(__file__).resolve().parent;load_dotenv(root/'.env')
    try:run(root/'data',args.backend,args.name,args.reset_lock)
    except (ValueError,OSError) as error:print('중단: '+str(error));raise SystemExit(1)
