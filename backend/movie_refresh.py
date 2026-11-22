"""Background recommendation refresh. Existing hand-maintained JSON files stay authoritative."""
import json
import logging
import os
import re
import tempfile
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from update_movie_audience import extract_audience

LOGGER = logging.getLogger("cinespot.refresh")
KOREA = timezone(timedelta(hours=9))
DAY = 24 * 60 * 60
HOUR = 60 * 60
STATE_VERSION = 3


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(value, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@contextmanager
def refresh_lock(path):
    """A process-exit-safe lock also prevents duplicate Uvicorn workers collecting together."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            pass
        yield locked
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def normalized_title(title):
    return re.sub(r"\s+", "", title).casefold()


class MovieRefreshService:
    def __init__(self, directory, fetcher, sources, get_title, usable_row):
        self.directory = directory
        self.state_path = directory / "movie_refresh_data.json"
        self.lock_path = directory / "movie_refresh.lock"
        self.fetcher = fetcher
        self.sources = sources
        self.get_title = get_title
        self.usable_row = usable_row
        self.stop_event = threading.Event()
        self.thread = None

    def state(self):
        value = read_json(self.state_path, {})
        if not isinstance(value, dict):
            raise ValueError("자동 갱신 데이터 형식을 확인해 주세요.")
        return value

    def source_rows(self, name, fallback):
        try:
            rows = self.state().get("sources", {}).get(name, {}).get("rows")
            if isinstance(rows, list) and rows:
                return rows
        except (ValueError, OSError):
            LOGGER.warning("자동 갱신 캐시를 읽지 못해 원본 API로 조회합니다.")
        return fallback()

    def approved_titles(self):
        return self.state().get("approved_titles", [])

    def recommendation_data(self):
        catalog = read_json(self.directory / "movie_catalog.json", {"movies": []})
        metadata = read_json(self.directory / "movie_metadata.json", {})
        state = self.state()
        titles = {movie["title"] for movie in catalog["movies"]}
        titles.update(state.get("approved_titles", []))
        result = {}
        for title in titles:
            existing = dict(metadata.get(title, {}))
            automatic = state.get("movies", {}).get(title, {})
            # A manual code correction must never inherit another film's audience.
            same_film = not existing.get("movie_code") or (
                existing.get("movie_code") == automatic.get("movie_code")
            )
            if same_film:
                if not existing.get("release_date"):
                    for field in ("movie_code", "release_date", "kobis_title"):
                        if automatic.get(field):
                            existing[field] = automatic[field]
                if automatic.get("audience_as_of", "") >= (existing.get("audience_as_of") or ""):
                    for field in ("audience", "audience_as_of", "audience_source"):
                        if field in automatic:
                            existing[field] = automatic[field]
            result[title] = existing
        status = {
            key: state.get(key)
            for key in ("last_attempt", "last_completed", "source_errors", "last_error")
        }
        status["pending_count"] = len(state.get("pending", {}))
        return result, status

    def start(self):
        if os.getenv("CINESPOT_AUTO_REFRESH", "1") == "0":
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._worker, name="movie-refresh", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=1)

    def _worker(self):
        while not self.stop_event.is_set():
            try:
                self.refresh_once()
            except Exception as error:
                # Exception strings may contain authenticated URLs. Log only the type.
                LOGGER.warning("자동 갱신 실패 (%s). 저장된 자료를 유지합니다.", type(error).__name__)
            self.stop_event.wait(60)

    def _save(self, state):
        if not self.stop_event.is_set():
            write_json(self.state_path, state)

    def refresh_once(self):
        with refresh_lock(self.lock_path) as locked:
            if not locked or self.stop_event.is_set():
                return
            state = self.state()
            now = time.time()
            if state.get("refresh_version") == STATE_VERSION and now < state.get("next_attempt_at", 0):
                return
            state["refresh_version"] = STATE_VERSION
            state["last_attempt"] = datetime.now(KOREA).isoformat(timespec="seconds")
            state["next_attempt_at"] = now + HOUR
            state["last_error"] = None
            self._save(state)
            try:
                self._refresh(state)
            except Exception as error:
                state["last_error"] = type(error).__name__
                self._save(state)
                raise

    def _refresh(self, state):
        LOGGER.info("영화 자동 갱신을 시작합니다.")
        sources = state.setdefault("sources", {})
        errors = {}
        for url, key, name in self.sources():
            if self.stop_event.is_set():
                return
            try:
                rows = self.fetcher(url, key, name)
                if self.stop_event.is_set():
                    return
                if rows:
                    sources[name] = {"rows": rows}
                    self._save(state)
                else:
                    errors[name] = "빈 응답"
            except Exception as error:
                errors[name] = type(error).__name__
        state["source_errors"] = errors
        catalog = read_json(self.directory / "movie_catalog.json", {"movies": []})
        original = read_json(self.directory / "movie_metadata.json", {})
        known_titles = {movie["title"] for movie in catalog["movies"]}
        approved = set(state.get("approved_titles", []))
        discovered = {
            self.get_title(row)
            for source in sources.values()
            for row in source["rows"]
            if self.usable_row(row) and self.get_title(row)
        }
        known_spelling = {normalized_title(title): title for title in sorted(known_titles | approved)}
        discovered = {known_spelling.get(normalized_title(title), title) for title in discovered}
        release_hints = {}
        for source in sources.values():
            for row in source["rows"]:
                title = self.get_title(row)
                value = str(row.get("opnDay") or "").strip()
                try:
                    date = datetime.strptime(value, "%Y-%m-%d").strftime("%Y%m%d")
                except ValueError:
                    continue
                release_hints.setdefault(normalized_title(title), set()).add(date)
        excluded = read_json(self.directory / "excluded_titles.json", [])
        excluded = {normalized_title(title) for title in excluded if isinstance(title, str)}
        titles = sorted((known_titles | approved | discovered), key=str.casefold)
        movies = state.setdefault("movies", {})
        pending = state.setdefault("pending", {})
        api_key = os.getenv("KOBIS_API_KEY", "").strip()
        had_failure = bool(errors)
        target_day = (datetime.now(KOREA).date() - timedelta(days=1)).isoformat()
        with requests.Session() as session:
            for title in titles:
                if self.stop_event.is_set():
                    return
                if title not in known_titles and normalized_title(title) in excluded:
                    continue
                info = dict(original.get(title, {}))
                auto = movies.get(title, {})
                if not info.get("movie_code"):
                    info.update(auto)
                elif info["movie_code"] == auto.get("movie_code"):
                    if auto.get("audience_as_of", "") >= (info.get("audience_as_of") or ""):
                        info.update(auto)
                if not info.get("release_date") or not info.get("movie_code"):
                    review = pending.get(title, {})
                    release_dates = sorted(release_hints.get(normalized_title(title), set()))
                    if (time.time() < review.get("retry_at", 0)
                            and review.get("release_dates") == release_dates
                            and review.get("matching_version") == STATE_VERSION):
                        continue
                    if not api_key:
                        had_failure = True
                        state["last_error"] = "KOBIS_API_KEY 미설정"
                        continue
                    try:
                        match = self._find_movie(session, title, api_key, release_dates)
                        if match is None:
                            pending[title] = {"reason": "동명 작품·표기·개봉일 확인 필요", "release_dates": release_dates, "matching_version": STATE_VERSION, "retry_at": time.time() + DAY}
                            self._save(state)
                            continue
                        if info.get("movie_code") and info["movie_code"] != match["movie_code"]:
                            pending[title] = {"reason": "기존 영화 코드와 후보가 달라 확인 필요", "release_dates": release_dates, "matching_version": STATE_VERSION, "retry_at": time.time() + DAY}
                            self._save(state)
                            continue
                        info.update(match)
                        movies[title] = {**movies.get(title, {}), **match}
                        # A new movie is promoted only after the film-only match succeeds.
                        if title not in known_titles:
                            approved.add(title)
                            state["approved_titles"] = sorted(approved)
                        pending.pop(title, None)
                        self._save(state)
                    except (requests.RequestException, ValueError, KeyError, TypeError):
                        pending[title] = {"reason": "영화 정보 조회 실패", "release_dates": release_dates, "matching_version": STATE_VERSION, "retry_at": time.time() + HOUR}
                        had_failure = True
                        self._save(state)
                        continue
                code = str(info.get("movie_code", ""))
                if not re.fullmatch(r"[0-9A-Za-z]{8}", code):
                    continue
                if (info.get("audience_as_of") or "") >= target_day and (info.get("audience") or 0) > 0:
                    continue
                try:
                    html = self._get(session,
                        "https://www.kobis.or.kr/kobis/business/mast/mvie/searchMovieDtl.do",
                        {"code": code, "sType": "stat"},
                    ).text
                    audience, as_of = extract_audience(html, code)
                    if audience <= 0:
                        raise ValueError("0명 통계는 추가 확인 필요")
                    if as_of < (info.get("audience_as_of") or ""):
                        continue
                    movies[title] = {
                        **movies.get(title, {}), "movie_code": code,
                        "audience": audience, "audience_as_of": as_of,
                        "audience_source": "KOBIS(발권)통계 · 전국",
                    }
                    pending.pop(title, None)
                    self._save(state)
                except (requests.RequestException, ValueError) as error:
                    detail = str(error) if isinstance(error, ValueError) else type(error).__name__
                    pending[title] = {
                        "reason": "관객 수 조회·통계 확인 필요", "detail": detail,
                        "retry_at": time.time() + HOUR,
                    }
                    had_failure = True
        if self.stop_event.is_set():
            return
        state["last_completed"] = datetime.now(KOREA).isoformat(timespec="seconds")
        state["next_attempt_at"] = time.time() + (HOUR if had_failure else DAY)
        self._save(state)
        LOGGER.info("영화 자동 갱신 완료. 확인 대상 %d개", len(pending))

    def _get(self, session, url, params):
        for attempt in range(3):
            if self.stop_event.wait(1 if attempt == 0 else 2 ** attempt):
                raise ValueError("종료 중")
            try:
                response = session.get(url, params=params, timeout=(5, 20))
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 2:
                    raise
                continue
            if response.status_code in {502, 503, 504} and attempt < 2:
                response.close()
                continue
            response.raise_for_status()
            response.encoding = "utf-8"
            return response
        raise ValueError("통신 재시도 실패")

    def _find_movie(self, session, title, api_key, release_dates=None):
        response = self._get(session,
            "https://www.kobis.or.kr/kobisopenapi/webservice/rest/movie/searchMovieList.json",
            {"key": api_key, "movieNm": title, "curPage": 1, "itemPerPage": 100},
        )
        result = response.json().get("movieListResult")
        if not isinstance(result, dict):
            raise ValueError("KOBIS 영화 응답 오류")
        candidates = result.get("movieList", [])
        if int(result["totCnt"]) > len(candidates):
            # Reuse the complete KOBIS candidate export already collected by the user.
            cached = read_json(self.directory / "movie_candidates.json", {}).get(title, {})
            cached_candidates = cached.get("candidates", [])
            if (cached.get("error") or not cached_candidates
                    or cached.get("total", 0) > len(cached_candidates)):
                return None
            candidates = cached_candidates
        # A source release date can disambiguate a same-title film or a shortened subtitle.
        if release_dates:
            if len(release_dates) != 1:
                return None
            matches = [movie for movie in candidates
                       if movie.get("typeNm") == "장편"
                       and movie.get("openDt") == release_dates[0]
                       and normalized_title(movie.get("movieNm", "")).startswith(
                           normalized_title(title)
                       )]
            matching_method = "부산 원본 개봉일·제목 대조 (자동 연결)"
        else:
            matches = [movie for movie in candidates
                       if normalized_title(movie.get("movieNm", "")) == normalized_title(title)
                       and movie.get("typeNm") == "장편"]
            matching_method = "동일 제목 장편 단일 후보 (자동 연결)"
        if len(matches) != 1:
            return None
        movie = matches[0]
        if not re.fullmatch(r"\d{8}", movie.get("openDt", "")):
            return None
        release = datetime.strptime(movie["openDt"], "%Y%m%d").date()
        if release > datetime.now(KOREA).date():
            return None
        return {
            "movie_code": movie["movieCd"], "kobis_title": movie["movieNm"],
            "release_date": release.isoformat(), "release_date_source": "KOBIS 영화목록",
            "matching_method": matching_method,
        }


def create_lifespan(service):
    @asynccontextmanager
    async def lifespan(app):
        service.start()
        try:
            yield
        finally:
            service.stop()
    return lifespan
