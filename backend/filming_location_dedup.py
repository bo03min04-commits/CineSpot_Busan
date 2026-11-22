"""Conservative cross-source deduplication of filming locations, without mutating inputs."""
import copy
import math
import re
from collections import defaultdict

def normalized(value):
    return re.sub(r'[\W_]+','',str(value or '')).casefold()

def address_key(value):
    value=str(value or '').strip().replace('부산광역시','부산')
    if not re.search(r'\d',value) or value in {'주소 없음','주소없음','미상','정보 없음'}:
        return ''
    return normalized(value)

def point(record):
    try:
        lat,lng=float(record.get('lat')),float(record.get('lng'))
        if math.isfinite(lat) and math.isfinite(lng) and 33<lat<39 and 124<lng<132:
            return lat,lng
    except (TypeError,ValueError):pass
    return None

def distance(a,b):
    lat1,lng1=a;lat2,lng2=b
    rad=math.pi/180
    value=math.sin((lat2-lat1)*rad/2)**2+math.cos(lat1*rad)*math.cos(lat2*rad)*math.sin((lng2-lng1)*rad/2)**2
    return 12742000*math.asin(min(1,math.sqrt(value)))

def movies(record):
    values=record.get('movies') or [record.get('movie')]
    return {normalized(value) for value in values if value}

def grounded(record):
    return bool(point(record) or address_key(record.get('address')))

def contradict(a,b):
    districts=[re.search(r'([가-힣]+[구군])(?:\s|$)',str(record.get('address') or '')) for record in (a,b)]
    if all(districts) and districts[0].group(1)!=districts[1].group(1):return True
    aa,bb=address_key(a.get('address')),address_key(b.get('address'))
    if aa and bb and aa!=bb:return True
    ap,bp=point(a),point(b)
    return bool(ap and bp and distance(ap,bp)>100)

def strong_match(a,b):
    if contradict(a,b):return False
    aa,bb=address_key(a.get('address')),address_key(b.get('address'))
    if aa and aa==bb:return True
    ap,bp=point(a),point(b)
    return bool(ap and bp and distance(ap,bp)<=20)

def merge_records(records):
    # Prefer useful coordinates and a full address over a placeholder record.
    ranked=sorted(records,key=lambda pair:(bool(point(pair[1])),bool(address_key(pair[1].get('address'))),len(pair[1].get('description') or '')),reverse=True)
    result=copy.deepcopy(ranked[0][1])
    titles={};sources=[];ids=[]
    for _,record in records:
        for title in record.get('movies') or [record.get('movie')]:
            if title:titles.setdefault(normalized(title),title)
        for source in record.get('sources') or []:
            if source not in sources:sources.append(source)
        if record.get('id') and record['id'] not in ids:ids.append(record['id'])
    for field in ['address','description']:
        if not result.get(field):
            result[field]=next((record.get(field) for _,record in ranked if record.get(field)),result.get(field))
    if not point(result):
        located=next((record for _,record in ranked if point(record)),None)
        if located:result['lat'],result['lng']=located['lat'],located['lng']
    result['movies']=sorted(titles.values(),key=str.casefold)
    if result['movies']:result['movie']=result['movies'][0]
    result['sources']=sources
    if len(records)>1:
        result['merged_ids']=ids
        result['merged_records']=[copy.deepcopy(record) for _,record in records]
    return result

def deduplicate_locations(locations):
    groups=defaultdict(list)
    for index,record in enumerate(locations):
        # Empty names are never sufficient evidence for merging.
        key=normalized(record.get('name')) or f'unnamed:{index}'
        groups[key].append((index,record))
    output=[]
    for records in groups.values():
        clusters=[];unresolved=[]
        for entry in records:
            if not grounded(entry[1]):unresolved.append(entry);continue
            candidates=[cluster for cluster in clusters if any(strong_match(entry[1],old[1]) for old in cluster)
                        and not any(contradict(entry[1],old[1]) for old in cluster)]
            if len(candidates)==1:candidates[0].append(entry)
            else:clusters.append([entry])
        # An unlocated same-name row is linked only to a unique grounded candidate
        # sharing a movie. Multiple plausible places stay separate.
        for entry in unresolved:
            title_set=movies(entry[1])
            candidates=[cluster for cluster in clusters if title_set and any(grounded(old[1]) and title_set & movies(old[1]) for old in cluster) and not any(contradict(entry[1],old[1]) for old in cluster)]
            if len(candidates)==1:candidates[0].append(entry)
            else:output.append((entry[0],copy.deepcopy(entry[1])))
        for cluster in clusters:output.append((min(index for index,_ in cluster),merge_records(cluster)))
    return [record for _,record in sorted(output,key=lambda entry:entry[0])]
