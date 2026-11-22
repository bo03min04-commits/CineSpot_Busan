"""Visible daily, weekly and day-range timetables; never invent unspecified days."""
import re

CURRENT = re.compile(r'^(영업\s*종료|영업\s*중|영업\s*전|운영\s*종료|운영\s*중|운영\s*전|브레이크타임|휴게시간|임시휴업|임시휴무|폐업)')
STATES = {'운영종료':'closed_now','운영중':'open_now','운영전':'before_open','영업종료':'closed_now','영업중':'open_now','영업전':'before_open','브레이크타임':'break','휴게시간':'break','임시휴업':'temporarily_closed','임시휴무':'temporarily_closed','폐업':'permanently_closed'}
TIME = re.compile(r'(\d{1,2}):(\d{2})\s*[-~–~∼]\s*(?:다음\s*날\s*)?(\d{1,2}):(\d{2})')
DAYS='월화수목금토일'

def scope(line):
    if re.match(r'^(매일|연중무휴)(?:\s|$)',line):
        match=re.match(r'^(매일|연중무휴)\s*',line)
        return list(DAYS),line[match.end():]
    if re.match(r'^평일(?:\s|$)',line): return list('월화수목금'),re.sub(r'^평일\s*','',line)
    if re.match(r'^주말(?:\s|$)',line): return list('토일'),re.sub(r'^주말\s*','',line)
    listed=re.match(r'^([월화수목금토일](?:요일)?(?:\s*[,·/]\s*[월화수목금토일](?:요일)?)+)(?=\s|$)',line)
    if listed: return re.findall(r'[월화수목금토일]',listed.group(1).replace('요일','')),line[listed.end():].strip()
    match=re.match(r'^([월화수목금토일])(?:요일)?\s*[-~–∼]\s*([월화수목금토일])(?:요일)?(?=\s|$)',line)
    if match:
        start,end=map(DAYS.index,match.groups())
        return [DAYS[(start+i)%7] for i in range((end-start)%7+1)],line[match.end():].strip()
    match=re.match(r'^([월화수목금토일])(?:요일)?(?=\s|$|\()',line)
    if match:
        rest=line[match.end():].strip()
        rest=re.sub(r'^\([^)]*\)\s*','',rest)
        return [match.group(1)],rest
    return None,line

def parse_section(lines):
    records={}; current=[]; state='unknown'; status_text=''
    for raw in lines:
        line=str(raw).strip()
        if not line or line in {'접기','펼쳐보기','더보기','영업시간','운영시간'} or '수정 제안' in line: continue
        holiday=re.search(r'매주\s*([월화수목금토일])요일',line) if '휴무' in line else None
        if holiday and records:
            day=holiday.group(1)
            records.setdefault(day,{'day':day,'hours':[],'breaks':[],'closed':False,'notes':[]})
            records[day]['closed']=True;records[day]['notes'].append(line)
            continue
        days,rest=scope(line)
        if days is not None:
            current=days
            if line.startswith('연중무휴') and not rest and records: continue
            for day in days:
                records[day]={'day':day,'hours':[],'breaks':[],'closed':False,'notes':[]}
            line=rest
            if not line: continue
        elif not current:
            match=CURRENT.match(line)
            if match and not status_text:
                state=STATES[re.sub(r'\s','',match.group(1))];status_text=line
                continue
            if '24시간' in line and re.search(r'영업|운영',line):
                current=list(DAYS)
                for day in current: records[day]={'day':day,'hours':[],'breaks':[],'closed':False,'notes':[]}
            else: continue
        for day in current:
            record=records[day]
            if re.search(r'24\s*시간',line):
                if '00:00–24:00' not in record['hours']: record['hours'].append('00:00–24:00')
                continue
            matches=list(TIME.finditer(line))
            if matches:
                field='breaks' if re.search(r'브레이크|휴게',line) else 'hours'
                for match in matches:
                    h1,m1,h2,m2=map(int,match.groups())
                    if h1>23 or h2>24 or max(m1,m2)>59 or (h2==24 and m2):
                        record['notes'].append(line);continue
                    if re.search(r'다음\s*날',match.group()) and (h2,m2)>=(h1,m1):
                        # Existing storage represents overnight intervals by end < start.
                        # Keep unsupported 24h+ ranges as notes rather than changing meaning.
                        record['notes'].append(line);continue
                    value=f'{h1:02d}:{m1:02d}–{h2:02d}:{m2:02d}'
                    if value not in record[field]: record[field].append(value)
            elif '휴무' in line and not re.search(r'휴무\s*(?:없|아님)|무휴',line):
                record['closed']=True;record['notes'].append(line)
            elif line not in record['notes']: record['notes'].append(line)
    ordered=[records[day] for day in DAYS if day in records]
    complete=len(ordered)==7 and all(row['closed'] or row['hours'] for row in ordered)
    return {'status':state,'status_text':status_text,'weekly_hours':ordered,
            'closed_days':[row['day'] for row in ordered if row['closed']],'complete_week':complete}
