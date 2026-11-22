import json
import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape

import requests

from update_movie_metadata import METADATA_PATH, save_metadata


def extract_audience(html, movie_code):
    # 공식(연감)통계와 구분하여 KOBIS 발권통계만 읽습니다.
    section = re.search(
        r"<strong\b[^>]*>\s*KOBIS\(발권\)통계\s*</strong>"
        r"(.*?)</table>",
        html,
        re.S,
    )
    if not section:
        raise ValueError("KOBIS 발권통계 표를 찾지 못했습니다.")

    content = section.group(1)
    compact = re.sub(r"\s+", "", content)
    if f"'kb','{movie_code}'" not in compact:
        raise ValueError("응답의 영화 코드를 확인하지 못했습니다.")

    text = unescape(re.sub(r"<[^>]+>", " ", content))
    date_match = re.search(
        r"통계기준일\s*:\s*(\d{4}\.\d{2}\.\d{2})",
        text,
    )
    if not date_match:
        raise ValueError("통계 기준일을 찾지 못했습니다.")

    as_of = datetime.strptime(
        date_match.group(1), "%Y.%m.%d"
    ).date().isoformat()

    results = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", content, re.S):
        cells = [
            unescape(re.sub(r"<[^>]+>", " ", cell)).strip()
            for cell in re.findall(
                r"<td\b[^>]*>(.*?)</td>", row, re.S
            )
        ]

        if len(cells) != 4 or cells[0] != "전국":
            continue

        number = re.fullmatch(
            r"(\d{1,3}(?:,\d{3})+|\d+)\s*\([^)]*\)",
            cells[3],
        )
        if number:
            results.append(int(number.group(1).replace(",", "")))

    if len(results) != 1:
        raise ValueError("전국 누적 관객 수를 확정하지 못했습니다.")

    return results[0], as_of


def main():
    metadata = json.loads(
        METADATA_PATH.read_text(encoding="utf-8-sig")
    )
    if not isinstance(metadata, dict):
        raise ValueError("movie_metadata.json 형식을 확인해 주세요.")

    target_day = (
        datetime.now(timezone(timedelta(hours=9))).date() - timedelta(days=1)
    ).isoformat()

    saved = 0
    missing = 0
    failed = 0

    with requests.Session() as session:
        titles = sorted(metadata, key=lambda title: title.casefold())
        if not titles:
            raise ValueError("저장된 영화 정보가 없습니다.")

        print(f"저장된 영화 정보 {len(titles)}개로 수집을 시작합니다.")

        for index, title in enumerate(titles, start=1):
            info = metadata.get(title, {})
            if not isinstance(info, dict):
                raise ValueError(f"{title}의 저장 형식을 확인해 주세요.")

            if (
                (info.get("audience") or 0) > 0
                and (info.get("audience_as_of") or "") >= target_day
            ):
                print(f"[{index}/{len(titles)}] {title}: 최신 기준일 수집 완료, 건너뜀")
                continue
            movie_code = str(info.get("movie_code") or "")
            if not re.fullmatch(r"[0-9A-Za-z]{8}", movie_code):
                missing += 1
                print(f"[{index}/{len(titles)}] {title}: 작품 연결 필요")
                continue

            time.sleep(1)
            print(
                f"[{index}/{len(titles)}] {title}: 관객 수 조회 중",
                flush=True,
            )

            try:
                response = session.get(
                    "https://www.kobis.or.kr/kobis/business/mast/"
                    "mvie/searchMovieDtl.do",
                    params={"code": movie_code, "sType": "stat"},
                    timeout=(10, 60),
                )
            except requests.Timeout:
                failed += 1
                print(f"[{index}/{len(titles)}] {title}: 시간 초과, 다음 영화 진행")
                continue
            except requests.ConnectionError:
                failed += 1
                print(f"[{index}/{len(titles)}] {title}: 연결 실패, 다음 영화 진행")
                continue

            if response.status_code != 200:
                raise ValueError(
                    f"HTTP {response.status_code} 응답으로 중단합니다. "
                    "지금까지 저장한 결과는 유지됩니다."
                )

            response.encoding = "utf-8"
            try:
                audience, as_of = extract_audience(
                    response.text, movie_code
                )
            except ValueError as error:
                failed += 1
                print(f"[{index}/{len(titles)}] {title}: {error}")
                continue

            if audience <= 0:
                failed += 1
                print(f"[{index}/{len(titles)}] {title}: 0명 통계 확인 필요")
                continue

            old_date = info.get("audience_as_of")
            if old_date and as_of < old_date:
                print(f"[{index}/{len(titles)}] {title}: 기존 자료가 더 최신")
                continue

            metadata[title] = {
                **info,
                "audience": audience,
                "audience_as_of": as_of,
                "audience_source": "KOBIS(발권)통계 · 전국",
            }
            save_metadata(metadata)
            saved += 1
            print(
                f"[{index}/{len(titles)}] {title}: "
                f"{audience:,}명 / {as_of} 저장"
            )

    print(
        f"\n완료: 저장 {saved}개, "
        f"작품 연결 필요 {missing}개, 통계 확인 필요 {failed}개"
    )


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as error:
        print(f"통신 오류 종류: {type(error).__name__}")
        print("저장된 결과는 유지됩니다.")
        raise SystemExit(1)
    except (ValueError, OSError) as error:
        print(f"중단: {error}")
        raise SystemExit(1)