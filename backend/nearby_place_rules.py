"""User-reviewed corrections, matched by both business name and street number."""
import json
import re
from pathlib import Path

def compact(value):
    return re.sub(r'[^0-9a-z가-힣]','',str(value).casefold())

def street(value):
    value=str(value).replace('부산광역시','부산')
    match=re.search(r'^(.+?(?:대로|로|길))\s*(\d+)(?:\s*-\s*(\d+))?',value)
    if not match:return compact(value)
    return compact(match.group(1))+':'+str(int(match.group(2)))+':'+str(int(match.group(3) or 0))

def rules():
    path=Path(__file__).resolve().parent.parent/'shared/nearbyCorrections.json'
    return json.loads(path.read_text(encoding='utf-8-sig'))

def rule_for(place):
    for rule in rules():
        if compact(place.get('name','')) in {compact(n) for n in rule['names']} and street(place.get('address',''))==street(rule['address']):
            return rule
    return None

def correct_mapping(place):
    rule=rule_for(place)
    if rule and rule['action']=='exclude':return None
    return {**place,'name':rule['name']} if rule and rule['action']=='rename' else dict(place)

def correct_place(place):
    value=correct_mapping(place.model_dump())
    return place.model_copy(update=value) if value is not None else None

def correct_places(places):
    return [value for place in places if (value:=correct_place(place)) is not None]
