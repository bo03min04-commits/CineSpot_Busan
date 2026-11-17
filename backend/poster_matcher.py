"""Offline poster reference matcher. Only user-supplied local images are indexed."""
from pathlib import Path
from io import BytesIO
from functools import lru_cache
import imagehash
import json
from PIL import Image, ImageOps, UnidentifiedImageError

POSTER_DIR = Path(__file__).resolve().parent / "posters"
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def poster_files():
    if not POSTER_DIR.exists():
        return []
    allow_path = POSTER_DIR.parent / "data" / "verified_movies.json"
    try:
        allowed = set(json.loads(allow_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []
    return sorted(p for p in POSTER_DIR.glob("*/*") if p.is_file() and p.suffix.lower() in EXTENSIONS and p.parent.name in allowed)


def poster_catalog():
    counts = {}
    for path in poster_files():
        counts[path.parent.name] = counts.get(path.parent.name, 0) + 1
    return [{"title": title, "posters": count} for title, count in sorted(counts.items())]


def hashes(image):
    image = ImageOps.exif_transpose(image).convert("RGB")
    w, h = image.size
    # Whole image + modest central crops allow some camera margins and borders.
    variants = [image]
    if min(w, h) >= 128:
        for margin in (0.05, 0.10):
            variants.append(image.crop((int(w*margin), int(h*margin), int(w*(1-margin)), int(h*(1-margin)))))
    return [imagehash.phash(v, hash_size=16) for v in variants]


@lru_cache(maxsize=8)
def cached_references(signature):
    entries = []
    for name, _, _ in signature:
        path = POSTER_DIR / name
        try:
            with Image.open(path) as im:
                entries.append((path.parent.name, path.name, hashes(im)))
        except (OSError, ValueError):
            continue
    return entries


def match_poster(data):
    try:
        with Image.open(BytesIO(data)) as im:
            if im.width * im.height > 25_000_000:
                raise ValueError("이미지 해상도가 너무 큽니다.")
            query_hashes = hashes(im)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("올바른 포스터 이미지가 아닙니다.")
    paths = poster_files()
    signature = tuple((str(p.relative_to(POSTER_DIR)), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
    entries = cached_references(signature)
    scores = {}
    for title, filename, reference_hashes in entries:
        # 256-bit pHash. Distance 0 means visually identical hash.
        distance = int(min(a-b for a in query_hashes for b in reference_hashes))
        if title not in scores or distance < scores[title][0]:
            scores[title] = (distance, filename)
    ranked = sorted(scores.items(), key=lambda x: x[1][0])
    # Conservative threshold. No forced guesses for unrelated images.
    candidates = [{"title": title, "distance": distance, "matched_file": filename}
                  for title, (distance, filename) in ranked if distance <= 60][:3]
    return {"candidates": candidates, "method": "local-phash", "registered_posters": len(entries),
            "requires_confirmation": True, "note": "거리 값은 확률이 아닙니다. 낮을수록 유사합니다."}
