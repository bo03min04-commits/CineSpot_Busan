import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = Path.home() / "Downloads" / "recommended_movies.json"
METADATA_PATH = BASE_DIR / "data" / "movie_metadata.json"
OUTPUT_PATH = BASE_DIR / "data" / "movie_candidates.json"

SEARCH_ALIASES = {
    "V.I.P. (브이아이피)": "브이아이피",
    "고사: 피의 중간고사": "고사",
    "넷플릭스 고백의역사": "고백",
    "넷플릭스 길복순": "길복순",
    "넷플릭스 모범가족": "모범가족",
    "눈에는 눈, 이에는 이": "눈에는 눈",
    "도굴(컬렉터)": "도굴",
    "마지막 선물...귀휴": "마지막 선물",
    "수(壽)": "수",
    "타짜: 신의 손": "타짜",
}

def main():
    load_dotenv(BASE_DIR / ".env")
    api_key = os.getenv("KOBIS_API_KEY", "").strip()
    if not api_key:
        raise ValueError("KOBIS_API_KEY를 확인해 주세요.")

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8-sig"))
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8-sig"))

    pending = [
        movie["title"]
        for movie in catalog["movies"]
        if not metadata.get(movie["title"], {}).get("movie_code")
    ]

    results = {}
    if OUTPUT_PATH.exists():
        results = json.loads(
            OUTPUT_PATH.read_text(encoding="utf-8-sig")
        )

    print(f"작품 연결 대상: {len(pending)}개")

    with requests.Session() as session:
        for index, title in enumerate(pending, start=1):
            # 성공한 후보 수집은 재실행 시 재사용합니다.
            previous = results.get(title, {})
            if (
                title in results
                and title not in SEARCH_ALIASES
                and not previous.get("error")
                and previous.get("total", 0)
                <= len(previous.get("candidates", []))
            ):
                print(f"[{index}/{len(pending)}] {title}: 후보 수집 완료")
                continue

            time.sleep(1)
            try:
                response = session.get(
                    "https://www.kobis.or.kr/kobisopenapi/"
                    "webservice/rest/movie/searchMovieList.json",
                    params={
                        "key": api_key,
                        "movieNm": SEARCH_ALIASES.get(title, title),
                        "curPage": 1,
                        "itemPerPage": 100,
                    },
                    timeout=(10, 40),
                )
                response.raise_for_status()
                payload = response.json()

                if "faultInfo" in payload:
                    raise ValueError(
                        "KOBIS API 오류입니다. 키 승인 상태와 호출 한도를 확인해 주세요."
                    )

                result = payload.get("movieListResult")
                if not isinstance(result, dict):
                    raise ValueError("KOBIS 응답 형식을 확인해 주세요.")

                total = int(result["totCnt"])
                candidates = list(result["movieList"])
                page = 1

                while len(candidates) < total:
                    page += 1
                    time.sleep(1)

                    response = session.get(
                        "https://www.kobis.or.kr/kobisopenapi/"
                        "webservice/rest/movie/searchMovieList.json",
                        params={
                            "key": api_key,
                            "movieNm": SEARCH_ALIASES.get(title, title),
                            "curPage": page,
                            "itemPerPage": 100,
                        },
                        timeout=(10, 40),
                    )
                    response.raise_for_status()
                    payload = response.json()

                    if "faultInfo" in payload:
                        raise ValueError(
                            "KOBIS API 오류입니다. 키와 호출 한도를 확인해 주세요."
                        )

                    next_result = payload.get("movieListResult")
                    if not isinstance(next_result, dict):
                        raise ValueError("KOBIS 응답 형식을 확인해 주세요.")

                    next_candidates = next_result.get("movieList", [])
                    known_codes = {
                        candidate["movieCd"] for candidate in candidates
                    }
                    new_candidates = [
                        candidate
                        for candidate in next_candidates
                        if candidate["movieCd"] not in known_codes
                    ]

                    if not new_candidates:
                        raise ValueError(
                            f"{title}: 다음 페이지 후보를 받지 못했습니다."
                        )

                    candidates.extend(new_candidates)

                results[title] = {
                    "total": total,
                    "candidates": candidates,
                }
                print(
                    f"[{index}/{len(pending)}] {title}: "
                    f"전체 {total}개, 저장 {len(candidates)}개"
                )
            except requests.RequestException as error:
                results[title] = {"error": type(error).__name__}
                print(f"[{index}/{len(pending)}] {title}: 통신 실패")

            temporary_path = OUTPUT_PATH.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary_path, OUTPUT_PATH)

    print(f"완료: {OUTPUT_PATH.name}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        print(f"중단: {error}")
        raise SystemExit(1)