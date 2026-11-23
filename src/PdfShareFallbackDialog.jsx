import { useEffect, useState } from 'react';
import { downloadCoursePdf } from './coursePdfShare';

function summaryText(course, mapUrl) {
  const route = Array.isArray(course?.route) ? course.route : [];
  return [
    `CineSpot 부산 | ${course?.title || '추천 여행 코스'}`,
    `영화: ${course?.movie || '부산 영화'}`,
    '',
    ...route.map((place, index) => `${index + 1}. ${place.name || '방문 장소'}${place.address ? ` — ${place.address}` : ''}`),
    ...(mapUrl ? ['', `카카오맵 길찾기: ${mapUrl}`] : []),
  ].join('\n');
}

export default function PdfShareFallbackDialog({ data, onClose }) {
  const [status, setStatus] = useState('');
  useEffect(() => {
    function onKeyDown(event) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  if (!data) return null;
  const { file, course, mapUrl } = data;

  async function copyCourse() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('클립보드를 지원하지 않는 브라우저입니다.');
      await navigator.clipboard.writeText(summaryText(course, mapUrl));
      setStatus('코스 내용과 지도 링크를 복사했어요. 카카오톡이나 메일에 붙여넣을 수 있어요.');
    } catch (error) {
      setStatus(`복사 실패: ${error?.message || '브라우저 권한을 확인해 주세요.'}`);
    }
  }

  return <div className="pdf-share-overlay" onMouseDown={(event) => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <section className="pdf-share-dialog" role="dialog" aria-modal="true" aria-labelledby="pdf-share-title" aria-describedby="pdf-share-description">
      <div className="pdf-share-header">
        <div><span className="pdf-share-eyebrow">CINESPOT · PDF COURSE</span><h2 id="pdf-share-title">코스 PDF 공유하기</h2></div>
        <button type="button" className="pdf-share-close" onClick={onClose} aria-label="공유 창 닫기">×</button>
      </div>
      <p id="pdf-share-description" className="pdf-share-description">
        이 브라우저에서는 PDF 파일을 다른 앱으로 직접 전달하는 공유 메뉴를 지원하지 않습니다.
        아래에서 PDF를 저장해 카카오톡·이메일에 첨부하거나 코스 내용을 복사할 수 있어요.
      </p>
      <div className="pdf-share-file">
        <span aria-hidden="true">PDF</span>
        <div><strong>{file.name}</strong><small>방문 장소 {course.route?.length || 0}곳 · PDF 파일 준비 완료</small></div>
      </div>
      <div className="pdf-share-actions">
        <button type="button" className="pdf-share-primary" onClick={() => {
          downloadCoursePdf(file);
          setStatus('PDF 다운로드를 시작했어요. 저장한 파일을 카카오톡이나 이메일에 첨부해 주세요.');
        }}>PDF 다운로드</button>
        <button type="button" className="pdf-share-secondary" onClick={copyCourse}>코스 내용 복사</button>
        {/^https:\/\/map\.kakao\.com\//.test(mapUrl || '') &&
          <a className="pdf-share-map" href={mapUrl} target="_blank" rel="noopener noreferrer">카카오맵 길찾기 열기 ↗</a>}
      </div>
      {status && <p className="pdf-share-status" role="status">{status}</p>}
      <p className="pdf-share-note">코스 내용 복사와 카카오맵 링크 공유는 PDF 파일 자체를 전송하는 기능이 아닙니다.</p>
    </section>
  </div>;
}
