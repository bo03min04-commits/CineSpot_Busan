import json
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
METADATA_PATH = BASE_DIR / "data" / "movie_metadata.json"
KOBIS_URL = (
    "https://www.kobis.or.kr/kobisopenapi/webservice/rest/"
    "movie/searchMovieList.json"
)


def normalize_title(title):
    return re.sub(r"\s+", "", title).casefold()


def save_metadata(metadata):
    # 임시 파일에 저장한 뒤 교체하여 저장 도중 파일이 손상되지 않게 합니다.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=METADATA_PATH.parent,
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(
                metadata,
                temporary_file,
                ensure_ascii=False,
                indent=2,
            )
            temporary_file.write("\n")

        os.replace(temporary_path, METADATA_PATH)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main():
    load_dotenv(BASE_DIR / ".env")
    api_key = os.getenv("KOBIS_API_KEY", "").strip()
    if not api_key:
        raise ValueError("backend/.env에 KOBIS_API_KEY를 추가해 주세요.")

    metadata = json.loads(
        METADATA_PATH.read_text(encoding="utf-8-sig")
    )
    if not isinstance(metadata, dict):
        raise ValueError("movie_metadata.json은 {} 형태여야 합니다.")

    with requests.Session() as session:
        response = session.get(
            "http://127.0.0.1:8000/api/movies",
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()

        if data.get("source_errors"):
            raise ValueError(
                "촬영지 원본 데이터에 오류가 있어 수집을 중단합니다. "
                "기존 영화 검색이 정상인지 확인해 주세요."
            )

        titles = data.get("movies")
        if not isinstance(titles, list) or not titles:
            raise ValueError("부산 촬영 영화 목록을 가져오지 못했습니다.")
        if not all(isinstance(title, str) for title in titles):
            raise ValueError("영화 제목 목록의 형식을 확인해 주세요.")

        saved = 0
        for index, title in enumerate(titles, start=1):
            existing = metadata.get(title, {})
            if not isinstance(existing, dict):
                raise ValueError(f"{title}의 저장 데이터 형식을 확인해 주세요.")

            if existing.get("release_date"):
                print(f"[{index}/{len(titles)}] {title}: 기존 개봉일 유지")
                continue

            time.sleep(0.3)
            response = session.get(
                KOBIS_URL,
                params={
                    "key": api_key,
                    "movieNm": title,
                    "curPage": 1,
                    "itemPerPage": 100,
                },
                timeout=30,
            )

            if response.status_code != 200:
                raise ValueError(
                    f"KOBIS 응답 오류 HTTP {response.status_code}. "
                    "지금까지 저장한 데이터는 유지됩니다."
                )

            payload = response.json()
            if "faultInfo" in payload:
                fault = payload["faultInfo"]
                raise ValueError(
                    f"KOBIS 오류 코드 {fault.get('errorCode', '확인 필요')}. "
                    "키 승인 상태와 호출 한도를 확인해 주세요."
                )

            result = payload.get("movieListResult")
            if not isinstance(result, dict):
                raise ValueError("KOBIS 영화 목록 응답 형식을 확인해 주세요.")

            candidates = result.get("movieList", [])
            if int(result.get("totCnt", 0)) > len(candidates):
                print(f"[{index}/{len(titles)}] {title}: 후보가 많아 수동 확인 필요")
                continue

            matches = [
                candidate
                for candidate in candidates
                if normalize_title(candidate.get("movieNm", ""))
                == normalize_title(title)
            ]

            if len(matches) != 1:
                print(f"[{index}/{len(titles)}] {title}: 일치 후보 {len(matches)}개, 확인 필요")
                continue

            match = matches[0]
            raw_date = match.get("openDt", "")
            if not re.fullmatch(r"\d{8}", raw_date):
                print(f"[{index}/{len(titles)}] {title}: 개봉일 정보 없음")
                continue

            release_date = datetime.strptime(
                raw_date, "%Y%m%d"
            ).date().isoformat()

            metadata[title] = {
                **existing,
                "movie_code": match["movieCd"],
                "release_date": release_date,
                "release_date_source": "KOBIS 영화목록",
            }
            save_metadata(metadata)
            saved += 1
            print(f"[{index}/{len(titles)}] {title}: {release_date} 저장")

        print(f"\n완료. 이번에 개봉일 {saved}개를 저장했습니다.")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException:
        # 요청 URL에 포함된 API 키가 터미널에 노출되지 않도록 합니다.
        print("통신에 실패했습니다. 서버 실행 상태와 네트워크를 확인해 주세요.")
        raise SystemExit(1)
    except (ValueError, OSError) as error:
        print(f"중단: {error}")
        raise SystemExit(1)