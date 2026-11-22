import json
import re
from datetime import datetime

from update_movie_metadata import BASE_DIR, METADATA_PATH, save_metadata


TITLE_CODES = {
    "V.I.P. (브이아이피)": "20164174",
    "고사: 피의 중간고사": "20081058",
    "눈에는 눈, 이에는 이": "20080968",
    "대외비: 권력의 탄생": "20208962",
    "도굴(컬렉터)": "20192193",
    "마지막 선물...귀휴": "20080014",
    "수(壽)": "20070066",
    "이대로, 죽을 순 없다": "20050189",
    "조폭 마누라 2": "20030138",
    "타짜: 신의 손": "20134804",
    "해무": "20134082",
    "홍반장": "20040471",
    "블랙 팬서": "20170561",
}


def normalize_title(title):
    return re.sub(r"\s+", "", title).casefold()


def main():
    candidates_path = BASE_DIR / "data" / "movie_candidates.json"
    candidates = json.loads(
        candidates_path.read_text(encoding="utf-8-sig")
    )
    metadata = json.loads(
        METADATA_PATH.read_text(encoding="utf-8-sig")
    )

    added = 0
    pending = []

    for title, result in candidates.items():
        # 앞서 연결한 작품과 관객 수는 보존합니다.
        if metadata.get(title, {}).get("movie_code"):
            continue

        if result.get("error"):
            pending.append(title)
            continue

        rows = result.get("candidates", [])
        if result.get("total", 0) > len(rows):
            pending.append(title)
            continue

        if title in TITLE_CODES:
            matches = [
                row for row in rows
                if row["movieCd"] == TITLE_CODES[title]
            ]
            method = "제목 표기 대조"
        else:
            matches = [
                row for row in rows
                if normalize_title(row["movieNm"]) == normalize_title(title)
                and row.get("typeNm") == "장편"
                and "한국" in row.get("nationAlt", "").split(",")
                and re.fullmatch(r"\d{8}", row.get("openDt", ""))
            ]
            method = "제목·한국 장편·개봉일 조건의 단일 후보"

        if len(matches) != 1:
            pending.append(title)
            continue

        match = matches[0]
        release_date = datetime.strptime(
            match["openDt"], "%Y%m%d"
        ).date().isoformat()

        metadata[title] = {
            **metadata.get(title, {}),
            "movie_code": match["movieCd"],
            "kobis_title": match["movieNm"],
            "release_date": release_date,
            "release_date_source": "KOBIS 영화목록",
            "directors": [
                director["peopleNm"]
                for director in match.get("directors", [])
            ],
            "matching_method": method,
        }
        added += 1
        print(f"{title}: {match['movieNm']} / {release_date}")

    save_metadata(metadata)

    print(f"\n추가 연결: {added}개")
    print(f"전체 저장 영화: {len(metadata)}개")
    print(f"남은 확인 대상: {len(pending)}개")
    for title in pending:
        print(f" - {title}")


if __name__ == "__main__":
    main()