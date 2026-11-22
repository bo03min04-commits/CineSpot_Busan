"""Conservative estimate from a complete weekly timetable, in Korea time."""
import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

def estimate(data, now=None):
    unknown = {'status': 'unknown', 'basis': 'schedule'}
    if not data.get('complete_week') or data.get('status') in {'temporarily_closed', 'permanently_closed'}:
        return unknown
    try:
        stamp = datetime.fromisoformat(data['checked_at'])
        if stamp.tzinfo is None: return unknown
        now = (now or datetime.now(KST)).astimezone(KST)
        if not 0 <= (now - stamp).total_seconds() <= 172800: return unknown
        rows = {row['day']: row for row in data['weekly_hours']}
        intervals, breaks = [], []
        for offset in (-1, 0):
            day = now.date() + timedelta(days=offset)
            row = rows.get('월화수목금토일'[day.weekday()])
            if row is None: return unknown
            if row.get('closed'): continue
            if row.get('notes'): return unknown
            if not row.get('hours'): return unknown
            for field, dest in [('hours', intervals), ('breaks', breaks)]:
                for text in row.get(field, []):
                    match = re.fullmatch(r'(\d{1,2}):(\d{2})[–~\-](\d{1,2}):(\d{2})', text.replace(' ', ''))
                    if not match: return unknown
                    h1, m1, h2, m2 = map(int, match.groups())
                    if h1 > 23 or h2 > 24 or max(m1, m2) > 59 or (h2 == 24 and m2): return unknown
                    start = datetime(day.year, day.month, day.day, tzinfo=KST) + timedelta(hours=h1, minutes=m1)
                    end = datetime(day.year, day.month, day.day, tzinfo=KST) + timedelta(hours=h2, minutes=m2)
                    if end == start: return unknown
                    if end < start: end += timedelta(days=1)
                    dest.append((start, end))
        status = 'closed_now'
        if any(start <= now < end for start, end in intervals):
            status = 'break' if any(start <= now < end for start, end in breaks) else 'open_now'
        return {'status': status, 'basis': 'schedule'}
    except (ValueError, KeyError, TypeError):
        return unknown
