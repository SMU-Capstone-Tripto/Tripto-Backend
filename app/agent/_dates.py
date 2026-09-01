"""여행 기간 파싱 공용 모듈.

traveldates 값의 형태가 코드마다 제각각으로 처리돼 왔다:
  - "2026-05-20 ~ 2026-05-23"  (표준)
  - "2026.05.20~2026.05.23"    (점 구분, 공백 없음)
  - "2026-05-20 - 2026-05-23"  (하이픈 구분)
  - {"start": "...", "end": "..."}  (state.py 타입힌트상 dict)
  - "2026-05-20"               (단일 날짜 → 당일치기)
파싱 실패 시 조용히 num_days=1로 떨어져 3박4일이 당일치기가 되는 버그가 있었다.
→ 여기서 한 번에 흡수한다.
"""
import re
from datetime import date

_DATE_RE = re.compile(r'(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})')


def parse_range(traveldates) -> tuple[date, date] | None:
    """traveldates → (start_date, end_date). 파싱 불가 시 None. 단일 날짜면 (d, d)."""
    if not traveldates:
        return None

    if isinstance(traveldates, dict):
        text = f"{traveldates.get('start', '')} ~ {traveldates.get('end', '')}"
    else:
        text = str(traveldates)

    found = _DATE_RE.findall(text)
    if not found:
        return None

    try:
        start = date(int(found[0][0]), int(found[0][1]), int(found[0][2]))
        end = (date(int(found[1][0]), int(found[1][1]), int(found[1][2]))
               if len(found) > 1 else start)
    except (ValueError, IndexError):
        return None

    if end < start:
        start, end = end, start
    return start, end


def num_days(traveldates) -> int:
    """여행 일수. 파싱 불가 시 1."""
    r = parse_range(traveldates)
    return (r[1] - r[0]).days + 1 if r else 1


def is_peak_season(traveldates) -> bool:
    """시작일이 성수기(여름 7/15~8/31, 겨울 12/20~1/10)인지."""
    r = parse_range(traveldates)
    if not r:
        return False
    m, d = r[0].month, r[0].day
    if (m == 7 and d >= 15) or m == 8:
        return True
    if (m == 12 and d >= 20) or (m == 1 and d <= 10):
        return True
    return False
