"""Portable public operating information, without browser jobs or credentials."""
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit

DATA_FIELDS={'status','status_text','weekly_hours','closed_days','complete_week','source_title','source_address','source_url','checked_at'}
ROW_FIELDS={'day','hours','breaks','closed','notes'}
MANUAL_FIELDS={'hours','closedDays','status','source','checkedOn','note','origin','matchedName','sourceUpdatedOn'}
HOSTS={'map.naver.com','pcmap.place.naver.com','m.place.naver.com','place.naver.com','naver.me'}

def safe_url(value):
    parsed=urlsplit(str(value or ''))
    if parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):return ''
    return urlunsplit((parsed.scheme,parsed.netloc,parsed.path,'',''))

def clean_data(data):
    if not isinstance(data,dict):return None
    value={key:data[key] for key in DATA_FIELDS if key in data}
    value['source_url']=safe_url(value.get('source_url'))
    if not value['source_url']:return None
    rows=data.get('weekly_hours')
    if not isinstance(rows,list):return None
    value['weekly_hours']=[{key:row[key] for key in ROW_FIELDS if key in row} for row in rows if isinstance(row,dict)]
    has_hours=any(not row.get('closed') and isinstance(row.get('hours'),list) and row['hours'] for row in value['weekly_hours'])
    if not has_hours and value.get('status')!='permanently_closed':return None
    return value

def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=20)

def export_data(directory):
    directory=Path(directory)
    output={'schema_version':1,'exported_at':datetime.now(timezone.utc).isoformat(),'naver':[],'manual':[]}
    path=directory/'naver_operating_info.sqlite3'
    if path.is_file():
        with closing(readonly(path)) as db:
            db.execute('BEGIN')
            rows=db.execute('SELECT s.id,s.name,s.address,s.url,c.data FROM sources s JOIN cache c ON s.id=c.id WHERE c.data IS NOT NULL').fetchall()
            for id,name,address,url,raw in rows:
                data=clean_data(json.loads(raw))
                if not data:continue
                output['naver'].append({'id':id,'name':name,'address':address,'url':safe_url(url) or data['source_url'],'data':data})
    path=directory/'place_operating_info.sqlite3'
    if path.is_file():
        with closing(readonly(path)) as db:
            for id,raw,revision in db.execute('SELECT id,value,revision FROM manual WHERE value IS NOT NULL'):
                if not re.fullmatch(r'\d{1,30}',str(id)):continue
                info=json.loads(raw)
                if isinstance(info,dict):output['manual'].append({'id':id,'value':{key:info[key] for key in MANUAL_FIELDS if key in info},'revision':revision})
    output['naver'].sort(key=lambda row:row['id']);output['manual'].sort(key=lambda row:row['id'])
    return output

def stamp(data):
    try:return datetime.fromisoformat(data.get('checked_at','')).timestamp()
    except (ValueError,TypeError,AttributeError):return 0

def import_shared_operating_info(directory):
    directory=Path(directory);seed=directory/'shared_operating_info.json'
    if not seed.is_file():return {'naver':0,'manual':0}
    raw=seed.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    payload=json.loads(raw.decode('utf-8-sig'))
    if payload.get('schema_version')!=1:raise ValueError('공유 운영정보 데이터 버전을 확인해 주세요.')
    path=directory/'naver_operating_info.sqlite3'
    count={'naver':0,'manual':0}
    with closing(sqlite3.connect(path,timeout=20)) as db,db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,name TEXT,address TEXT,url TEXT);
            CREATE TABLE IF NOT EXISTS cache(id TEXT PRIMARY KEY,data TEXT,error TEXT,retry REAL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS shared_seed_state(id TEXT PRIMARY KEY);
        ''')
        if db.execute('SELECT 1 FROM shared_seed_state WHERE id=?',(digest,)).fetchone():return count
        for row in payload.get('naver',[]):
            data=clean_data(row.get('data'));url=safe_url(row.get('url'))
            if not data or not url or not all(isinstance(row.get(k),str) and row[k] for k in ('id','name','address')):continue
            current=db.execute('SELECT name,address,url FROM sources WHERE id=?',(row['id'],)).fetchone()
            if current and tuple(current)!=(row['name'],row['address'],url):continue
            db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?)',(row['id'],row['name'],row['address'],url))
            cached=db.execute('SELECT data FROM cache WHERE id=?',(row['id'],)).fetchone()
            existing=json.loads(cached[0]) if cached and cached[0] else None
            if existing and stamp(existing)>=stamp(data):continue
            db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?,?)',(row['id'],json.dumps(data,ensure_ascii=False),'',0));count['naver']+=1
    path=directory/'place_operating_info.sqlite3'
    with closing(sqlite3.connect(path,timeout=20)) as db,db:
        db.execute('CREATE TABLE IF NOT EXISTS manual(id TEXT PRIMARY KEY,value TEXT,revision INTEGER NOT NULL)')
        for row in payload.get('manual',[]):
            if not re.fullmatch(r'\d{1,30}',str(row.get('id',''))) or not isinstance(row.get('value'),dict):continue
            value={key:row['value'][key] for key in MANUAL_FIELDS if key in row['value']}
            changed=db.execute('INSERT OR IGNORE INTO manual VALUES(?,?,?)',(row['id'],json.dumps(value,ensure_ascii=False),max(1,int(row.get('revision',1))))).rowcount
            count['manual']+=changed
    with closing(sqlite3.connect(directory/'naver_operating_info.sqlite3',timeout=20)) as db,db:
        db.execute('INSERT OR IGNORE INTO shared_seed_state VALUES(?)',(digest,))
    return count

if __name__=='__main__':
    counts=import_shared_operating_info(Path(__file__).resolve().parent/'data')
    print('공유 운영정보 가져오기: '+str(counts))
