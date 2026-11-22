"""Find a public Naver venue by name/address, then read its visible operating section."""
import re
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import quote, urlsplit, urlunsplit

from naver_hours_parser import parse_section
from naver_debug_capture import event, session
from naver_place_matching import match_identity, compact, compatible_name, spoken_initials, name_variants

HOSTS = {'naver.me', 'map.naver.com', 'm.place.naver.com', 'pcmap.place.naver.com', 'place.naver.com'}
STOP = re.compile(r'^(?:전화번호|전화|가격표|메뉴|편의|주차|홈페이지|정보\s*수정|업체\s*정보|주소|찾아가는길)(?:\s|$)')
BLOCK = re.compile(r'자동입력\s*방지|로봇이\s*아닙니다|비정상적인\s*접근|서비스\s*접근이\s*제한|보안\s*인증을\s*완료')


def allowed(url):
    try:
        value = urlsplit(url)
        return value.scheme == 'https' and value.hostname in HOSTS and not value.username and not value.password and value.port in (None, 443)
    except ValueError:
        return False


def norm(text):
    return re.sub(r'[^0-9a-z가-힣]', '', str(text).casefold())


def address_key(text):
    text = re.sub(r'\([^)]*\)', '', text)
    text = re.sub(r'\s+\d+층.*$', '', text)
    return norm(text).replace('부산광역시', '부산')


def section(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    start = next((i for i, line in enumerate(lines) if re.match(r'^(?:영업|운영)\s*시간', line)), None)
    if start is None:
        start = next((i for i, line in enumerate(lines) if re.match(r'^(?:영업\s*중|영업\s*종료|영업\s*전|24\s*시간\s*(?:영업|운영))', line)), None)
    if start is None:
        return []
    output = []
    for i in range(start, min(start + 70, len(lines))):
        if i > start and (STOP.match(lines[i]) or re.search(r'방문자\s*리뷰|블로그\s*리뷰', lines[i])):
            break
        output.append(lines[i][:300])
    return output


def identity_match(text, title, name, address, phone=''):
    return match_identity(text, title, name, address, phone)


def place_url(page_url, frame_url):
    for value in (frame_url, page_url):
        path = urlsplit(value).path
        match = re.search(r'/entry/place/(\d+)', path) or re.search(r'/[A-Za-z_-]+/(\d{5,})(?:/|$)', path)
        if match:
            return 'https://map.naver.com/p/entry/place/' + match.group(1)
    value = urlsplit(page_url)
    return urlunsplit((value.scheme, value.netloc, value.path, '', ''))


def search_url(name, address):
    return 'https://map.naver.com/p/search/' + quote(f'{name} {address}'.strip(), safe='')

def is_detail(frame_url, frame_name):
    # /p/entry/place/ID is the outer map shell, not the business content.
    parsed=urlsplit(frame_url)
    return frame_name=='entryIframe' or (
        parsed.hostname in {'pcmap.place.naver.com','m.place.naver.com','place.naver.com'}
        and bool(re.match(r'^/(?:restaurant|place|cafe|parking|attraction|accommodation|hospital|beauty|local)/\d+',parsed.path)))

def search_targets(url,name,address,phone=''):
    if url and not allowed(url):
        raise ValueError('[INVALID_URL] HTTPS 네이버 장소 주소만 사용할 수 있습니다.')
    # A verified detail URL gets one direct attempt; supplied search/phone URLs
    # are replaced with identity-aware searches rather than used as the only query.
    direct=bool(url and '/search/' not in urlsplit(url).path)
    targets=[url] if direct else []
    clean=re.sub(r'\([^)]*\)','',address).strip()
    clean=re.sub(r'\s+\d+층.*$','',clean)
    tokens=clean.split()
    district=' '.join(tokens[:3] if len(tokens)>2 and tokens[2].endswith(('읍','면','동')) else tokens[:2])
    variants=name_variants(name)
    queries=[f'{name} {clean}']
    if len(variants)>1:queries.append(f'{variants[1]} {clean}')
    queries.append(f'{district} {name}')
    digits=re.sub(r'\D','',phone)
    # Shared 15xx/16xx call-centre numbers do not identify an individual branch.
    if len(digits)>=9 and digits.startswith('0'):queries.append(digits+' '+district+' '+name)
    for query in queries:
        value='https://map.naver.com/p/search/'+quote(query.strip(),safe='')
        if value not in targets:targets.append(value)
    return targets[:3]

def candidate_name(label):
    # Naver may glue a category/review count onto the visible venue name.
    value=label.splitlines()[0].strip()
    value=re.split(r'리뷰\s*\d|방문자\s*리뷰',value)[0].strip()
    for category in ('카페,디저트','치킨,닭강정','돼지고기구이','한식','중식당','영화관','박물관','공연장','피자','주차장'):
        if value.endswith(category):
            stem=value[:-len(category)].strip()
            if stem:return stem
    return value


def _collect_one(url, name, address, headless=True, phone='', seconds=22):
    target = url or search_url(name, address)
    if not allowed(target):
        raise ValueError('[INVALID_URL] HTTPS 네이버 장소 주소만 사용할 수 있습니다.')
    try:
        from playwright.sync_api import Error as BrowserError, TimeoutError as BrowserTimeout
    except ImportError:
        raise ValueError('[BROWSER_SETUP] 백엔드 가상환경에 playwright가 필요합니다.') from None
    browser = _get_browser(headless)
    context = None
    started = time.monotonic()
    deadline = started + seconds
    seen_detail = matched_place = saw_hours = False
    clicked_hours = set()
    tried_results = set()
    stable = {}
    found = None
    last_browser_error = False
    last_selection = 0
    empty_since = {}
    try:
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(1500)
        try:
            page.goto(target, wait_until='domcontentloaded', timeout=12000)
        except BrowserTimeout:
            last_browser_error = True
        while time.monotonic() < deadline:
            if page.url != 'about:blank' and not allowed(page.url):
                raise ValueError('[REDIRECT] 네이버 이외 페이지로 이동했습니다.')
            for frame in page.frames[:12]:
                if time.monotonic() >= deadline: break
                if not allowed(frame.url): continue
                try:
                    text = frame.locator('body').inner_text(timeout=1200)
                    if BLOCK.search(text):
                        raise ValueError('[BLOCKED] 접근 제한·보안 확인으로 중단했습니다.')
                    frame_path = urlsplit(frame.url).path
                    detail = is_detail(frame.url,frame.name)
                    seen_detail = seen_detail or detail
                    if re.search(r'검색\s*결과가\s*없|검색된\s*결과가\s*없|조건에\s*맞는\s*업체가\s*없',text) and not detail:
                        empty_since.setdefault(frame.url,time.monotonic())
                        if time.monotonic()-empty_since[frame.url]>=3:
                            raise ValueError('[SEARCH_EMPTY] 네이버 화면에 조건에 맞는 업체가 없다고 표시됩니다.')
                        continue
                    empty_since.pop(frame.url,None)
                    lines = section(text)
                    data = parse_section(lines)
                    title = page.title()
                    actual_name = identity_match(text, title, name, address, phone) if detail else ''
                    event('page', frame_url=frame.url, frame_name=frame.name, title=title, text=text[:12000], hours_section=lines, parsed=data, matched_name=actual_name)
                    if detail and not actual_name:
                        observed=[line.strip() for line in text.splitlines() if line.strip()]
                        observed_address=next((observed[i+1] for i,value in enumerate(observed[:-1]) if value=='주소'),'')
                        event('identity_mismatch',visible_header=observed[:8],visible_address=observed_address,expected_name=name,expected_address=address,frame_url=frame.url)
                    matched_place = matched_place or bool(actual_name)
                    saw_hours = saw_hours or bool(actual_name and lines)
                    if actual_name and (data['weekly_hours'] or data['status'] != 'unknown'):
                        value = {**data, 'source_title': actual_name, 'source_address': address,
                                 'checked_at': datetime.now(timezone.utc).isoformat(),
                                 'source_url': place_url(page.url, frame.url)}
                        if not found or len(value['weekly_hours']) > len(found['weekly_hours']): found = value
                        if data['complete_week'] or data['status'] == 'permanently_closed': return found
                    # Try an ordinary visible expansion control. Avoid collapsing an already open table.
                    if actual_name and lines and frame.url not in clicked_hours:
                        controls = frame.locator('a[role="button"], button').filter(has_text=re.compile(r'영업|휴무|운영|^펼쳐보기$'))
                        choices = []
                        for control in controls.all()[:20]:
                            label = control.inner_text(timeout=700).strip()
                            if not control.is_visible() or len(label)>250 or re.search(r'수정|제안|신고|문의|로그인|접기|정책|약관|고객센터',label): continue
                            score = 2*bool(re.search(r'펼|더보기',label)) + bool(re.search(r'영업시간|운영시간',label))
                            if label=='펼쳐보기':
                                parent=control.locator('xpath=..').inner_text(timeout=700)
                                if len(parent)>500 or not re.search(r'영업|운영|휴무',parent):continue
                            if score==0:continue
                            choices.append((score,control))
                            event('expansion_candidate', label=label, score=score)
                        if choices:
                            choices.sort(key=lambda pair: pair[0],reverse=True)
                            if len(choices)==1 or choices[0][0]>choices[1][0]:
                                event('expansion_click', label=choices[0][1].inner_text(timeout=700)[:250])
                                choices[0][1].click(timeout=1500)
                                clicked_hours.add(frame.url)
                                stable.clear()
                                continue
                    is_list = 'search' in frame.name.casefold() or '/list' in frame_path
                    if not detail and is_list and len(tried_results)<3 and time.monotonic()-last_selection>=3:
                        prefixes=list(dict.fromkeys(compact(spelling)[:4] for variant in name_variants(name) for spelling in (variant,spoken_initials(variant))))
                        expression='(?:'+'|'.join(r'[\s·.\-]*'.join(re.escape(char) if char!='앤' else r'(?:앤|&|and)' for char in prefix) for prefix in prefixes if prefix)+')'
                        candidates = frame.get_by_text(re.compile(expression,re.I)).all()
                        for candidate in candidates[:20]:
                            label = candidate.inner_text(timeout=700).strip()
                            marker = (frame.url,label)
                            cleaned=candidate_name(label)
                            compatible=compatible_name(name,label) or compatible_name(name,cleaned)
                            event('search_candidate', label=label[:250], compatible=compatible)
                            if marker in tried_results or len(label)>100 or not candidate.is_visible() or not compatible: continue
                            tried_results.add(marker)
                            last_selection = time.monotonic()
                            event('search_click',label=label[:250])
                            candidate.click(timeout=1500)
                            stable.clear()
                            break
                    if detail and text.strip():
                        fingerprint = (frame.url,text)
                        previous = stable.get(frame.url)
                        if previous is None or previous[0]!=fingerprint:
                            stable[frame.url] = (fingerprint,time.monotonic())
                        elif time.monotonic()-previous[1]>=7:
                            if actual_name and found: return found
                            if actual_name and not lines:
                                raise ValueError('[HOURS_NOT_LISTED] 같은 장소를 확인했지만 운영시간 영역이 없습니다.')
                            if actual_name and lines and any(re.match(r'^(?:영업|운영)시간\s*정보\s*수정',line) for line in lines):
                                raise ValueError('[HOURS_NOT_LISTED] 네이버 상세 페이지에 시간표 대신 정보 수정·추가만 표시됩니다.')
                            if actual_name and lines and any('정보없음' in line.replace(' ','') for line in lines):
                                raise ValueError('[HOURS_NOT_LISTED] 시간표 영역에 정보없음으로 표시됩니다.')
                            if actual_name and lines:
                                raise ValueError('[HOURS_FORMAT] 운영시간 영역은 있으나 시간표를 해석하지 못했습니다.')
                            raise ValueError('[MATCH_FAILED] 상세 페이지 이름·주소·전화 대조가 일치하지 않습니다.')
                except BrowserError as error:
                    event('browser_error', error=str(error)[:1000])
                    last_browser_error = True
                    continue
            time.sleep(.5)
        if found: return found
        if matched_place and saw_hours: code,message='HOURS_FORMAT','시간표 형식 해석 또는 펼치기에 실패했습니다.'
        elif matched_place: code,message='HOURS_NOT_LISTED','같은 장소를 확인했지만 운영시간을 찾지 못했습니다.'
        elif seen_detail: code,message='MATCH_FAILED','장소 상세 화면의 이름·주소·전화 대조에 실패했습니다.'
        elif last_browser_error: code,message='LOAD_TIMEOUT','검색·상세 페이지 로딩에 실패했습니다.'
        else: code,message='SEARCH_SELECTION','검색 결과에서 대상 장소를 선택하지 못했습니다.'
        raise ValueError(f'[{code}] {message}')
    except BrowserError:
        raise ValueError('[BROWSER_CONNECTION] 네이버 페이지 연결이 끊겼습니다.') from None
    finally:
        if context is not None:
            try: context.close()
            except BrowserError: pass

def _collect(url,name,address,headless=True,phone='',direct_only=False):
    from nearby_place_rules import correct_mapping,rule_for
    original={'name':name,'address':address}
    corrected=correct_mapping(original)
    if corrected is None:raise ValueError('[EXCLUDED] 사용자 확인으로 삭제한 주변 장소입니다.')
    name=corrected['name']
    rule=rule_for(original)
    if rule and rule.get('query_url'):url=rule['query_url']
    deadline=time.monotonic()+60
    best=None
    errors=[]
    planned=search_targets(url,name,address,phone)
    for target in planned[:1] if direct_only else planned:
        remaining=deadline-time.monotonic()
        if remaining<3:break
        event('search_attempt',url=target)
        try:
            data=_collect_one(target,name,address,headless,phone,min(22,remaining))
            if any(row.get('hours') for row in data.get('weekly_hours',[])) or data.get('status')=='permanently_closed':
                return data
            best=data
        except ValueError as error:
            message=str(error)
            event('attempt_failed',url=target,error=message)
            # Identity has been verified in these cases: changing search words
            # cannot create hours, and must not substitute another business.
            if any('['+code+']' in message for code in ('BLOCKED','BROWSER_SETUP','INVALID_URL','REDIRECT','HOURS_NOT_LISTED','HOURS_FORMAT')):
                raise
            errors.append(message)
    if best:return best
    # Preserve the most informative identity failure over a later empty query.
    message=next((m for m in errors if '[MATCH_FAILED]' in m),errors[-1] if errors else '[LOAD_TIMEOUT] 조회 제한 시간에 도달했습니다.')
    raise ValueError(message)


# Playwright objects stay on one dedicated thread, including across FastAPI requests.
from concurrent.futures import Future
from queue import Queue
from threading import Thread, Lock
import atexit

class BrowserWorker:
    def __init__(self):
        self.queue = Queue()
        self.lock = Lock()
        self.closed = False
        self.thread = Thread(target=self.run, name='naver-browser', daemon=True)
        self.thread.start()

    def submit(self, function, *args):
        future = Future()
        with self.lock:
            if self.closed:
                raise RuntimeError('Browser worker stopped')
            self.queue.put((future, function, args))
        return future

    def run(self):
        while True:
            item = self.queue.get()
            if item is None:
                return
            future, function, args = item
            if future.set_running_or_notify_cancel():
                try: future.set_result(function(*args))
                except BaseException as error: future.set_exception(error)

    def shutdown(self, wait=False):
        with self.lock:
            self.closed = True
            self.queue.put(None)
        if wait:
            self.thread.join(timeout=10)

_POOL = BrowserWorker()
_DRIVER = None
_BROWSER = None
_MODE = None


def _get_browser(headless):
    global _DRIVER, _BROWSER, _MODE
    from playwright.sync_api import sync_playwright, Error
    if _BROWSER is not None and _BROWSER.is_connected() and _MODE == headless:
        return _BROWSER
    if _BROWSER is not None:
        try: _BROWSER.close()
        except Error: pass
    if _DRIVER is None:
        _DRIVER = sync_playwright().start()
    try:
        _BROWSER = _DRIVER.chromium.launch(channel='msedge', headless=headless)
        _MODE = headless
        return _BROWSER
    except Error:
        raise ValueError('수집용 Edge를 시작하지 못했습니다. 설치 상태를 확인해 주세요.') from None


def _recorded_collect(url, name, address, headless, phone, direct_only=False):
    with session(url, name, address, phone):
        return _collect(url, name, address, headless, phone, direct_only)


def collect(url, name, address, headless=True, phone='',direct_only=False):
    return _POOL.submit(_recorded_collect, url, name, address, headless, phone,direct_only).result()


def _close_browser():
    global _DRIVER, _BROWSER
    if _BROWSER is not None:
        try: _BROWSER.close()
        except Exception: pass
    if _DRIVER is not None:
        try: _DRIVER.stop()
        except Exception: pass
    _DRIVER = _BROWSER = None


def _shutdown():
    try: _POOL.submit(_close_browser).result(timeout=10)
    except Exception: pass
    _POOL.shutdown(wait=False)

atexit.register(_shutdown)
