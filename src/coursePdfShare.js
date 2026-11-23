/**
 * CineSpot 부산 코스 공유용 PDF 생성기.
 * 한글을 Canvas에 렌더링한 후 JPEG 페이지를 PDF로 묶습니다.
 * 외부 라이브러리·API 키·서버 호출이 필요하지 않습니다.
 */

const PAPER = { width: 1240, height: 1754, pdfWidth: 595.28, pdfHeight: 841.89 };
const FONT = '"Noto Sans KR", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif';
const COLORS = {
  navy: '#16243C', blue: '#3479F6', ink: '#222D40', muted: '#728094',
  pale: '#F5F8FD', line: '#E2E8F1', white: '#FFFFFF',
};

function stopLabel(kind) {
  return { film: '영화 촬영지', food: '맛집', spot: '관광지', parking: '주차장' }[kind] || '방문 장소';
}

function metersBetween(a, b) {
  const latA = Number(a?.lat); const lngA = Number(a?.lng);
  const latB = Number(b?.lat); const lngB = Number(b?.lng);
  if (![latA, lngA, latB, lngB].every(Number.isFinite)) return 0;
  const rad = Math.PI / 180;
  const dLat = (latB - latA) * rad;
  const dLng = (lngB - lngA) * rad;
  const value = Math.sin(dLat / 2) ** 2 + Math.cos(latA * rad) * Math.cos(latB * rad) * Math.sin(dLng / 2) ** 2;
  return 12742000 * Math.atan2(Math.sqrt(value), Math.sqrt(Math.max(0, 1 - value)));
}

function formatDistance(meters) {
  if (meters >= 1000) return `${(meters / 1000).toFixed(1)}km`;
  return `${Math.round(meters)}m`;
}

function rectangle(ctx, x, y, w, h, r, color) {
  const rad = Math.min(r, w / 2, h / 2);
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(x + rad, y);
  ctx.lineTo(x + w - rad, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + rad);
  ctx.lineTo(x + w, y + h - rad);
  ctx.quadraticCurveTo(x + w, y + h, x + w - rad, y + h);
  ctx.lineTo(x + rad, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - rad);
  ctx.lineTo(x, y + rad);
  ctx.quadraticCurveTo(x, y, x + rad, y);
  ctx.closePath();
  ctx.fill();
}

function linesFor(ctx, text, maxWidth, maxLines) {
  const result = [];
  let current = '';
  for (const ch of Array.from(String(text || ''))) {
    if (ch === '\n' || (current && ctx.measureText(current + ch).width > maxWidth)) {
      result.push(current);
      current = ch === '\n' ? '' : ch;
      if (result.length >= maxLines) break;
    } else {
      current += ch;
    }
  }
  if (result.length < maxLines && current) result.push(current);
  // 두 줄까지만 표시하는 긴 제목의 경우, 마지막 줄 말줄임표 처리
  if (result.length === maxLines && ctx.measureText(result[result.length - 1]).width > maxWidth) {
    let tail = result[result.length - 1];
    while (tail && ctx.measureText(tail + '…').width > maxWidth) tail = tail.slice(0, -1);
    result[result.length - 1] = tail + '…';
  }
  return result;
}

function textLines(ctx, text, x, y, maxWidth, lineHeight, maxLines = 2) {
  const lines = linesFor(ctx, text, maxWidth, maxLines);
  lines.forEach((line, i) => ctx.fillText(line, x, y + i * lineHeight));
  return lines.length;
}

function makePageCanvas(course, stops, pageIndex, pageCount, totalMeters, link) {
  const canvas = document.createElement('canvas');
  canvas.width = PAPER.width;
  canvas.height = PAPER.height;
  const ctx = canvas.getContext('2d', { alpha: false });
  if (!ctx) throw new Error('PDF 렌더링을 위한 Canvas를 만들 수 없습니다.');

  const W = PAPER.width;
  const H = PAPER.height;
  const margin = 82;
  ctx.fillStyle = COLORS.pale;
  ctx.fillRect(0, 0, W, H);

  ctx.fillStyle = COLORS.blue;
  ctx.font = `800 39px ${FONT}`;
  ctx.fillText('CineSpot 부산', margin, 100);
  ctx.fillStyle = COLORS.muted;
  ctx.font = `600 21px ${FONT}`;
  ctx.fillText('MY FILM TRAVEL COURSE', margin, 135);
  ctx.fillStyle = COLORS.ink;
  ctx.font = `800 48px ${FONT}`;
  textLines(ctx, course.title || `${course.movie || '부산 영화'} 탐방 코스`, margin, 216, W - margin * 2, 58, 1);
  ctx.fillStyle = COLORS.muted;
  ctx.font = `500 22px ${FONT}`;
  const time = course.createdAt && !Number.isNaN(new Date(course.createdAt).getTime())
    ? new Date(course.createdAt).toLocaleDateString('ko-KR')
    : new Date().toLocaleDateString('ko-KR');
  ctx.fillText(`${time}  ·  ${pageIndex + 1} / ${pageCount}페이지`, margin, 254);

  const cards = [
    { label: '방문 장소', value: `${course.route.length}곳` },
    { label: '총 이동거리', value: formatDistance(totalMeters) },
    { label: '예상 도보시간', value: totalMeters > 0 ? `약 ${Math.max(1, Math.round(totalMeters / 4000 * 60))}분` : '계산 불가' },
  ];
  const cardGap = 18;
  const cardWidth = (W - 2 * margin - 2 * cardGap) / 3;
  cards.forEach((card, i) => {
    const x = margin + i * (cardWidth + cardGap);
    rectangle(ctx, x, 294, cardWidth, 126, 19, COLORS.white);
    ctx.fillStyle = COLORS.muted;
    ctx.font = `500 21px ${FONT}`;
    ctx.fillText(card.label, x + 26, 335);
    ctx.fillStyle = COLORS.navy;
    ctx.font = `800 36px ${FONT}`;
    ctx.fillText(card.value, x + 26, 388);
  });

  ctx.fillStyle = COLORS.navy;
  ctx.font = `800 29px ${FONT}`;
  ctx.fillText('추천 방문 순서', margin, 482);

  const start = pageIndex * 6;
  const visible = stops.slice(start, start + 6);
  visible.forEach((stop, i) => {
    const y = 510 + i * 166;
    rectangle(ctx, margin, y, W - 2 * margin, 149, 16, COLORS.white);
    rectangle(ctx, margin + 20, y + 29, 62, 62, 18, COLORS.blue);
    ctx.fillStyle = COLORS.white;
    ctx.textAlign = 'center';
    ctx.font = `800 27px ${FONT}`;
    ctx.fillText(String(start + i + 1), margin + 51, y + 71);
    ctx.textAlign = 'start';

    ctx.fillStyle = COLORS.navy;
    ctx.font = `800 27px ${FONT}`;
    textLines(ctx, stop.name || '방문 장소', margin + 106, y + 46, 700, 33, 2);

    ctx.fillStyle = COLORS.muted;
    ctx.font = `500 20px ${FONT}`;
    textLines(ctx, stop.address || ' ', margin + 106, y + 117, 730, 25, 1);

    ctx.fillStyle = COLORS.blue;
    ctx.font = `700 20px ${FONT}`;
    ctx.textAlign = 'right';
    ctx.fillText(stopLabel(stop.kind), W - margin - 22, y + 43);
    ctx.fillStyle = COLORS.muted;
    ctx.font = `500 19px ${FONT}`;
    ctx.fillText(start + i === 0 ? '출발' : `이전 장소에서 ${formatDistance(stop.segmentMeters)}`, W - margin - 22, y + 123);
    ctx.textAlign = 'start';
  });

  ctx.fillStyle = COLORS.line;
  ctx.fillRect(margin, 1580, W - 2 * margin, 2);
  ctx.fillStyle = COLORS.muted;
  ctx.font = `500 18px ${FONT}`;
  ctx.fillText('이동거리 및 도보시간은 방문시간을 제외한 예상값입니다.', margin, 1622);
  if (link) {
    rectangle(ctx, margin, 1651, W - 2 * margin, 67, 15, COLORS.navy);
    ctx.fillStyle = COLORS.white;
    ctx.textAlign = 'center';
    ctx.font = `700 23px ${FONT}`;
    ctx.fillText(course.route.length >= 2 && course.route.length <= 7
      ? '카카오맵에서 코스 길찾기 열기  →'
      : '카카오맵에서 출발지 열기  →', W / 2, 1693);
    ctx.textAlign = 'start';
  }
  return canvas;
}

function dataUrlToBytes(dataUrl) {
  const comma = dataUrl.indexOf(',');
  const binary = atob(dataUrl.slice(comma + 1));
  const result = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) result[i] = binary.charCodeAt(i);
  return result;
}

function escapePdfLiteral(value) {
  return String(value).replaceAll('\\', '\\\\').replaceAll('(', '\\(').replaceAll(')', '\\)');
}

function makePdfFromJpegs(jpegs, link) {
  const encoder = new TextEncoder();
  const chunks = [];
  const offsets = [];
  let byteLength = 0;
  function push(chunk) {
    const bytes = typeof chunk === 'string' ? encoder.encode(chunk) : chunk;
    chunks.push(bytes);
    byteLength += bytes.byteLength;
  }
  function beginObject(n) {
    offsets[n] = byteLength;
    push(`${n} 0 obj\n`);
  }
  function endObject() { push('endobj\n'); }

  push('%PDF-1.4\n%PDF-CineSpot\n');
  beginObject(1);
  push('<< /Type /Catalog /Pages 2 0 R >>\n');
  endObject();

  const ids = jpegs.map((_, i) => ({ page: 3 + i * 4, content: 4 + i * 4, image: 5 + i * 4, link: 6 + i * 4 }));
  beginObject(2);
  push(`<< /Type /Pages /Kids [${ids.map(({ page }) => `${page} 0 R`).join(' ')}] /Count ${ids.length} >>\n`);
  endObject();

  jpegs.forEach((bytes, i) => {
    const id = ids[i];
    beginObject(id.page);
    push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAPER.pdfWidth} ${PAPER.pdfHeight}] `);
    push(`/Resources << /XObject << /Im${i} ${id.image} 0 R >> >> `);
    push(`/Contents ${id.content} 0 R`);
    if (link) push(` /Annots [${id.link} 0 R]`);
    push(' >>\n');
    endObject();

    const instructions = `q\n${PAPER.pdfWidth} 0 0 ${PAPER.pdfHeight} 0 0 cm\n/Im${i} Do\nQ\n`;
    beginObject(id.content);
    push(`<< /Length ${encoder.encode(instructions).length} >>\nstream\n`);
    push(instructions);
    push('endstream\n');
    endObject();

    beginObject(id.image);
    push(`<< /Type /XObject /Subtype /Image /Width ${PAPER.width} /Height ${PAPER.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${bytes.length} >>\nstream\n`);
    push(bytes);
    push('\nendstream\n');
    endObject();

    beginObject(id.link);
    if (link) {
      // PDF 좌표는 왼쪽 하단이 원점입니다. 페이지 하단 버튼 영역에 링크를 겁니다.
      push(`<< /Type /Annot /Subtype /Link /Rect [38 17 557 51] /Border [0 0 0] /A << /S /URI /URI (${escapePdfLiteral(link)}) >> >>\n`);
    } else {
      push('<< >>\n');
    }
    endObject();
  });

  const infoId = 3 + jpegs.length * 4;
  beginObject(infoId);
  push('<< /Title (CineSpot Busan Travel Course) /Creator (CineSpot Busan Web App) >>\n');
  endObject();

  const xrefAt = byteLength;
  push(`xref\n0 ${infoId + 1}\n0000000000 65535 f \n`);
  for (let i = 1; i <= infoId; i++) push(`${String(offsets[i]).padStart(10, '0')} 00000 n \n`);
  push(`trailer\n<< /Size ${infoId + 1} /Root 1 0 R /Info ${infoId} 0 R >>\nstartxref\n${xrefAt}\n%%EOF\n`);

  return new Blob(chunks, { type: 'application/pdf' });
}

export function buildCoursePdfFile(course, mapUrl = '') {
  const route = Array.isArray(course?.route) ? course.route : [];
  if (!route.length) throw new Error('공유할 코스가 없습니다.');

  const stops = route.map((stop, i) => ({
    ...stop,
    segmentMeters: i > 0 ? metersBetween(route[i - 1], stop) : 0,
  }));
  const totalMeters = stops.reduce((total, stop) => total + stop.segmentMeters, 0);
  const pageCount = Math.ceil(stops.length / 6);
  const link = /^https:\/\/map\.kakao\.com\//.test(mapUrl) ? mapUrl : '';
  const jpegs = Array.from({ length: pageCount }, (_, i) => {
    const canvas = makePageCanvas(course, stops, i, pageCount, totalMeters, link);
    return dataUrlToBytes(canvas.toDataURL('image/jpeg', 0.88));
  });
  const pdf = makePdfFromJpegs(jpegs, link);
  const movie = String(course.movie || '부산영화').replace(/[\\/:*?"<>|\x00-\x1F]/g, '_').slice(0, 60);
  return new File([pdf], `CineSpot_${movie}_추천코스.pdf`, { type: 'application/pdf' });
}

export function downloadCoursePdf(file) {
  const href = URL.createObjectURL(file);
  const link = document.createElement('a');
  link.href = href;
  link.download = file.name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 30000);
}

/**
 * 사용자의 PDF 공유 버튼 클릭에서 직접 호출합니다.
 * 모바일 등 파일 공유를 지원하는 브라우저는 기기의 공유 메뉴를 열고,
 * 지원하지 않는 브라우저는 파일을 자동 다운로드하지 않고 대체 메뉴를 보여줍니다.
 */
export function shareCoursePdf(course, mapUrl, onMessage = () => {}, onFallback = () => {}) {
  try {
    const file = buildCoursePdfFile(course, mapUrl);
    if (navigator.share && navigator.canShare?.({ files: [file] })) {
      // 사용자 클릭 직후 공유 API를 호출해 user activation을 유지합니다.
      navigator.share({
        files: [file],
        title: course.title || 'CineSpot 부산 추천 코스',
        text: `${course.movie || '부산 영화'} 탐방 코스`,
      }).then(() => {
        onMessage('PDF 공유 메뉴를 통해 파일을 전달했어요.');
      }).catch((error) => {
        if (error?.name === 'AbortError') return; // 공유 취소 시 다운로드하지 않습니다.
        onFallback({ file, course, mapUrl });
        onMessage('이 기기에서 공유 메뉴를 열지 못해 다른 공유 방법을 보여드려요.');
      });
      return;
    }
    onFallback({ file, course, mapUrl });
    onMessage('PDF 파일을 준비했어요. 아래에서 공유 방법을 선택하세요.');
  } catch (error) {
    onMessage(`PDF 생성에 실패했어요: ${error?.message || '알 수 없는 오류'}`);
  }
}
