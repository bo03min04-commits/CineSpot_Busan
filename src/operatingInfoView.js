const LABELS = { open_now: '영업 중', closed_now: '영업 종료', before_open: '영업 전', break: '휴게시간', temporarily_closed: '임시휴업', permanently_closed: '폐업' };
export function operatingInfoView(result, connectionError = '', kind = '') {
  const info = result?.data;
  const rows = Array.isArray(info?.weekly_hours) ? info.weekly_hours : [];
  const days = new Map(rows.map(row => [row.day, row]));
  const allClosed = [...'월화수목금토일'].every(day => days.get(day)?.closed === true);
  const hasSchedule = rows.some(row => row.closed || row.hours?.length) && !allClosed;
  if (hasSchedule) {
    const observed = info.status_fresh && LABELS[info.status];
    const estimated = ['open_now', 'closed_now', 'before_open', 'break'].includes(info.estimated?.status) ? LABELS[info.estimated.status] : ''; 
    return { hasSchedule: true, suspicious: false, badge: observed ? `${observed} · 최근 조회` : estimated ? `${estimated} · 시간표 기준` : '운영시간 안내',
      status: observed ? info.status : estimated ? info.estimated.status : 'unknown', message: '' };
  }
  const permanentlyClosed = !allClosed && info?.status_fresh && info.status === 'permanently_closed';
  return { hasSchedule: false, suspicious: allClosed, badge: permanentlyClosed ? '폐업 안내 · 최근 조회' : '방문 전 확인', status: permanentlyClosed ? 'permanently_closed' : 'unknown',
    message: permanentlyClosed ? '최근 조회한 자료에 폐업으로 안내되어 있습니다. 상세정보를 확인해 주세요.' : allClosed ? '영업 여부와 운영시간을 다시 확인해야 합니다. 방문 전 매장에 문의해 주세요.' : kind === 'parking' ? '주차장 운영시간은 현장 안내 또는 관리처에 확인해 주세요.' : '운영시간을 확인하지 못했습니다. 방문 전 매장에 문의해 주세요.' };
}

export function phoneHref(phone) {
  const value = String(phone || '').replace(/[^\d+]/g, '');
  return /^\+?\d{7,15}$/.test(value) ? `tel:${value}` : '';
}

export function safeHttpLink(url) {
  try { const value = new URL(url); return ['http:', 'https:'].includes(value.protocol) ? value.href : ''; }
  catch { return ''; }
}
