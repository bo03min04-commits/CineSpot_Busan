"""현재 실행 중인 CineSpot API에서 1차 영화 후보 목록을 가져와 포스터 폴더를 만듭니다.
실행: python sync_poster_folders.py
"""
import csv
import json
import re
import urllib.request
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000/api/movies"
POSTER_DIR = Path(__file__).resolve().parent / "posters"
INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def main():
    try:
        with urllib.request.urlopen(BASE_URL, timeout=90) as response:
            data = json.load(response)
    except Exception as exc:
        raise SystemExit(f"영화 목록을 불러오지 못했습니다. 백엔드 실행/API 설정을 확인하세요: {exc}")
    errors = data.get('source_errors') or []
    if errors:
        print('주의: 일부 API 조회 오류로 목록이 불완전할 수 있습니다:')
        for error in errors: print(' -', error)
        answer = input('불완전한 목록으로 폴더를 생성할까요? (yes 입력): ').strip().lower()
        if answer != 'yes':
            raise SystemExit('중단했습니다. API 오류 해결 후 다시 실행하세요.')
    movies = sorted({str(v).strip() for v in data.get('movies', []) if str(v).strip()}, key=str.casefold)
    if not movies:
        raise SystemExit('영화 목록이 비어 있습니다. 폴더를 만들지 않았습니다.')
    POSTER_DIR.mkdir(parents=True, exist_ok=True)
    rows=[]
    used={}
    for title in movies:
        folder = INVALID.sub('_', title).rstrip(' .')
        if not folder or folder in {'.', '..'}: folder = '이름없음'
        original=folder; num=2
        while folder.casefold() in used and used[folder.casefold()] != title:
            folder=f'{original}_{num}';num+=1
        used[folder.casefold()]=title
        (POSTER_DIR/folder).mkdir(exist_ok=True)
        rows.append((title,folder))
    csv_path=POSTER_DIR/'영화_전체_목록.csv'
    with csv_path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['영화 제목','폴더 이름']);writer.writerows(rows)
    txt_path=POSTER_DIR/'영화_전체_목록.txt'
    txt_path.write_text(f'총 {len(rows)}편\n\n'+'\n'.join(f'{i}. {title}' for i,(title,_) in enumerate(rows,1))+'\n',encoding='utf-8')
    print(f'완료: 영화 후보 {len(rows)}개, 폴더 생성/확인 {len(rows)}개 (기존 비영화 폴더는 삭제하지 않음)')
    print('목록:',txt_path)
    print('CSV:',csv_path)

if __name__=='__main__': main()
