"""Match visible business identities without accepting different street numbers."""
import re
from difflib import SequenceMatcher

def compact(value):
    value=re.sub(r'\band\b','앤',str(value),flags=re.I).replace('&','앤')
    return re.sub(r'[^0-9a-z가-힣]','',value.casefold())

def road(value):
    value=re.sub(r'\([^)]*\)','',str(value))
    match=re.search(r'([가-힣0-9·]+(?:대로|로|길))\s*(\d+)(?:\s*-\s*(\d+))?',value)
    district=re.search(r'([가-힣]+[구군])(?:\s|$)',value)
    return (match.group(1),int(match.group(2)),int(match.group(3) or 0),district.group(1) if district else '') if match else None

def same_address(expected,visible):
    cities=[]
    for value in (expected,visible):
        city=re.search(r'(부산(?:광역시)?|서울(?:특별시)?|인천(?:광역시)?|대구(?:광역시)?|광주(?:광역시)?|대전(?:광역시)?|울산(?:광역시)?|세종(?:특별자치시)?)(?:\s|$)',value)
        cities.append(re.sub(r'광역시|특별시|특별자치시','',city.group(1)) if city else '')
    if all(cities) and cities[0]!=cities[1]: return False
    wanted=road(expected)
    if wanted is None: return compact(expected)==compact(visible)
    actual=road(visible)
    return bool(actual and wanted[:3]==actual[:3] and (not wanted[3] or not actual[3] or wanted[3]==actual[3]))

def name_core(value):
    value=re.sub(r'\s*-\s*네이버\s*(?:지도|플레이스)\s*$','',value).strip()
    # Remove only a separate final branch token; never truncate a concatenated brand.
    value=re.sub(r'\s+[^\s]+점$','',value)
    return compact(value).replace('앤델리','')

def spoken_initials(value):
    # Only isolated upper-case abbreviations. Do not transliterate arbitrary brands.
    alphabet=dict(zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ',
        ['에이','비','씨','디','이','에프','지','에이치','아이','제이','케이','엘','엠','엔','오','피','큐','알','에스','티','유','브이','더블유','엑스','와이','지']))
    return re.sub(r'(?<![A-Za-z])[A-Z]{2,6}(?![A-Za-z])',
                  lambda m: ''.join(alphabet[c] for c in m.group()),value)

def branch(value):
    match=re.search(r'\s+([^\s]+점)$',value.strip())
    return compact(match.group(1)).removeprefix('부산') if match else ''

FACILITY_KIND=re.compile(r'(연극장|소극장|대극장|극장|전망대|박물관|도서관|미술관|공연장|전시장|체험관|콘서트홀|아트홀)$')

def name_variants(value):
    """Shorten a separated parent prefix, keeping a distinctive child name.

    Never drop the child suffix to use the parent's hours, and never reduce
    a branch name or generic labels such as '소극장' to a loose search term.
    """
    value=str(value).strip()
    output=[value]
    words=value.split()
    if len(words)>1 and words[-1].endswith('점'):
        brand=' '.join(words[:-1])
        if len(compact(brand))>=4:output.append(brand)
    for index in range(1,len(words)):
        child=' '.join(words[index:])
        normalized=compact(child)
        kind=FACILITY_KIND.search(normalized)
        if kind and len(normalized[:kind.start()])>=2:
            output.append(child)
    return list(dict.fromkeys(output))

def compatible_name(expected,actual,phone_match=False):
    if compact(expected)==compact(actual): return True
    # The same branch can include a city prefix or omit whitespace.
    for separated,other in ((expected,actual),(actual,expected)):
        words=separated.split()
        if len(words)>1 and words[-1].endswith('점'):
            brand=compact(' '.join(words[:-1]));tail=compact(words[-1]).removeprefix('부산').removesuffix('점')
            value=compact(other)
            if len(brand)>=4 and value.startswith(brand) and value[len(brand):].removeprefix('부산').removesuffix('점')==tail:return True
    # Different explicit branch names remain different even when their brands match.
    branches=[re.search(r'\s+([^\s]+점)$',value.strip()) for value in (expected,actual)]
    if all(branches) and branch(expected)!=branch(actual): return False
    if compact(actual) in [compact(v) for v in name_variants(expected)[1:]]:return True
    if compact(expected) in [compact(v) for v in name_variants(actual)[1:]]:return True
    if compact(spoken_initials(expected))==compact(spoken_initials(actual)): return True
    branches=[re.search(r'\s+([^\s]+점)$',value.strip()) for value in (expected,actual)]
    if all(branches) and branch(expected)!=branch(actual): return False
    first,second=name_core(expected),name_core(actual)
    if len(first)>=4 and first==second: return True
    if phone_match:
        # An institution prefix or a trailing Latin subtitle, with the same
        # independently checked street number and phone, can explain a longer name.
        if len(compact(expected))>=6 and compact(actual).endswith(compact(expected)):
            prefix=compact(actual)[:-len(compact(expected))]
            if prefix.endswith(('대학교','대학','시립','국립')): return True
        subtitle=re.sub(r'\s+[A-Za-z][A-Za-z\s&.-]*$','',actual).strip()
        if compact(subtitle)==compact(expected): return True
    return bool(phone_match and min(len(first),len(second))>=4 and SequenceMatcher(None,first,second).ratio()>=.78)

def match_identity(text,title,name,address,phone=''):
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    # Identity must come from the business header, never a review mentioning it.
    address_index=next((i for i,line in enumerate(lines) if line=='주소'),len(lines))
    cut=next((i for i,line in enumerate(lines) if i>address_index and re.match(r'^(?:방문자\s*리뷰|블로그\s*리뷰|AI 브리핑)',line)),len(lines))
    lines=lines[:cut]
    address_lines=[]
    for i,line in enumerate(lines[:-1]):
        if line=='주소': address_lines.append(lines[i+1])
    address_ok=any(same_address(address,line) for line in address_lines)
    digits=re.sub(r'\D','',phone)
    phones=[re.sub(r'\D','',value) for value in re.findall(r'0\d{1,3}[-\s]?\d{3,4}[-\s]?\d{4}','\n'.join(lines))]
    phone_ok=len(digits)>=8 and digits in phones
    names=lines[:8]
    # A top-level map title can lag behind the iframe. Use the iframe header.
    if address_ok:
        return next((candidate for candidate in names if len(candidate)<=100 and compatible_name(name,candidate,phone_ok)),'')
    # A differing known street number cannot be overridden by a common phone.
    visible_roads=[road(line) for line in address_lines if road(line)]
    if phone_ok and not (road(address) and visible_roads):
        return next((candidate for candidate in names if compact(name)==compact(candidate)),'')
    return ''
