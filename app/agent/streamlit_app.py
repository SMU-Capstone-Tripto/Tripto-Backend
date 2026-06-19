import sys
import os
import re
import json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from _naver_api import geocode as _naver_geocode

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

st.set_page_config(
    page_title="tripto",
    page_icon="✈️",
    layout="wide",
)


# ── 그래프 ────────────────────────────────────────────────────────────

@st.cache_resource
def get_graph():
    from graph import app
    return app


_CONFIRM_WORDS = [
    "네", "예", "맞아", "맞아요", "확인", "ok", "오케이",
    "좋아", "진행", "맞습니다", "맞음", "ㅇㅇ", "응", "그래",
    "correct", "yes", "괜찮아", "괜찮습니다", "오키",
]


def _is_confirmation(text: str) -> bool:
    t = text.strip().lower()
    return any(w in t for w in _CONFIRM_WORDS)


# ── 세션 초기화 ──────────────────────────────────────────────────────

def _init_session():
    for k, v in {
        "chat_history": [],
        "graph_state":  None,
        "phase":        "start",
        "result":       None,
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ── 지도 헬퍼 ────────────────────────────────────────────────────────

def _parse_coord(item: dict) -> tuple[float, float] | None:
    try:
        lat = float(item.get("mapy", 0))
        lon = float(item.get("mapx", 0))
        return (lat, lon) if lat and lon else None
    except Exception:
        return None


def _build_coord_lookup(result: dict) -> dict[str, tuple[float, float]]:
    """장소명 → (lat, lon)"""
    lookup = {}
    for key in ("tourist_spots", "restaurants", "cafes", "accommodations"):
        for item in (result.get(key) or []):
            title = item.get("title", "")
            coord = _parse_coord(item)
            if title and coord:
                lookup[title] = coord
    return lookup


def _extract_day_locations(
    schedule_lines: list[str],
    lookup: dict[str, tuple[float, float]],
) -> list[tuple[str, tuple[float, float]]]:
    """일정 줄에서 장소명 파싱 후 좌표 조회 (중복 제거, 순서 유지)"""
    locations: list[tuple[str, tuple[float, float]]] = []
    seen: set[str] = set()
    for line in schedule_lines:
        line = line.strip()
        if not line or not line[0].isdigit():
            continue
        m = re.search(r'\(([^)]+)\)\s*$', line)
        if m:
            place = m.group(1).strip()
            if place not in seen and place in lookup:
                locations.append((place, lookup[place]))
                seen.add(place)
                continue
        for name, coord in lookup.items():
            if name in line and name not in seen:
                locations.append((name, coord))
                seen.add(name)
                break
    return locations


_MARKER_COLORS = ["#2563eb", "#16a34a", "#dc2626", "#9333ea",
                  "#ea580c", "#0891b2", "#65a30d", "#db2777"]


def _make_naver_map_html(
    locations: list[tuple[str, tuple[float, float]]],
    fallback_center: tuple[float, float] = (37.5665, 126.9780),
    height: int = 520,
    acc_indices: set[int] | None = None,
) -> str:
    """acc_indices: 숙소에 해당하는 index 집합 (🏨 아이콘 및 별도 색상 적용)"""
    if locations:
        center_lat = sum(c[0] for _, c in locations) / len(locations)
        center_lon = sum(c[1] for _, c in locations) / len(locations)
    else:
        center_lat, center_lon = fallback_center

    _acc = acc_indices or set()
    loc_js = json.dumps([
        {
            "name":  name,
            "lat":   lat,
            "lng":   lon,
            "color": "#f59e0b" if i in _acc else _MARKER_COLORS[i % len(_MARKER_COLORS)],
            "isAcc": i in _acc,
        }
        for i, (name, (lat, lon)) in enumerate(locations)
    ])

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    body {{ margin: 0; padding: 0; }}
    #map {{ width: 100%; height: {height}px; }}
  </style>
</head>
<body>
  <div id="map"></div>
  <script>
    var map = L.map('map').setView([{center_lat}, {center_lon}], 13);
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      attribution: '© OpenStreetMap contributors',
      maxZoom: 19
    }}).addTo(map);

    var locations = {loc_js};
    var latlngs = [];

    locations.forEach(function(loc, i) {{
      var pos = [loc.lat, loc.lng];
      latlngs.push(pos);

      var inner = loc.isAcc
        ? '🏨'
        : (i + 1);
      var size = loc.isAcc ? '18px' : '13px';

      var icon = L.divIcon({{
        html: '<div style="background:' + loc.color + ';color:#fff;border-radius:50%;' +
              'width:32px;height:32px;display:flex;align-items:center;' +
              'justify-content:center;font-size:' + size + ';font-weight:700;' +
              'box-shadow:0 2px 8px rgba(0,0,0,.4);">' + inner + '</div>',
        className: '',
        iconSize: [32, 32],
        iconAnchor: [16, 16],
        popupAnchor: [0, -20]
      }});

      L.marker(pos, {{ icon: icon }})
        .addTo(map)
        .bindPopup('<div style="font-size:13px;font-weight:600;">' +
          (loc.isAcc ? '🏨 ' : (i + 1) + '. ') + loc.name + '</div>');
    }});

    if (latlngs.length > 1) {{
      L.polyline(latlngs, {{
        color: '#2563eb',
        weight: 3,
        opacity: 0.7,
        dashArray: '8, 6'
      }}).addTo(map);
    }}

    if (latlngs.length > 0) {{
      map.fitBounds(latlngs, {{ padding: [40, 40] }});
    }}
  </script>
</body>
</html>"""


# ── 일정 탭 렌더링 ───────────────────────────────────────────────────

def _render_schedule_line(line: str):
    # ② 이동 항목: "A → B (수단·시간·비용)"
    if "→" in line and not line[0].isdigit():
        # 장소 부분과 괄호 안 이동 정보 분리
        arrow_m = re.match(r'^(.+?)\s*\((.+)\)\s*$', line)
        if arrow_m:
            places = arrow_m.group(1).strip()
            detail = arrow_m.group(2).strip()
            st.markdown(
                f"<div style='padding:3px 0 3px 1rem;font-size:0.83rem;"
                f"color:#6b7280;border-left:2px solid #e5e7eb;margin:2px 0 2px 4px;'>"
                f"<span style='color:#374151;font-weight:600;'>↳ {places}</span>"
                f"<span style='margin-left:6px;color:#9ca3af;'>({detail})</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<div style='padding:3px 0 3px 1rem;font-size:0.83rem;"
                f"color:#6b7280;border-left:2px solid #e5e7eb;margin:2px 0 2px 4px;'>"
                f"↳ {line}</div>",
                unsafe_allow_html=True,
            )
        return

    # ② 활동 항목: "HH:MM~HH:MM 내용"
    range_m  = re.match(r'^(\d{2}:\d{2})~(\d{2}:\d{2})\s+(.*)', line)
    # ① 이벤트 항목: "HH:MM 내용 (도착/출발/귀환/체크인/체크아웃)"
    single_m = re.match(r'^(\d{2}:\d{2})\s+(.*)', line)

    _EVENT_KW = ("도착", "출발", "귀환", "체크인", "체크아웃")

    # 활동 유형별 배경색·좌측 강조선 색
    def _activity_style(body: str) -> tuple[str, str]:
        b = body
        if any(k in b for k in ("관광", "방문", "탐방", "투어", "체험")):
            return "#eff6ff", "#2563eb"   # 파랑 계열 — 관광
        if any(k in b for k in ("식사", "아침", "점심", "저녁", "브런치", "맛집")):
            return "#fff7ed", "#f97316"   # 주황 계열 — 식사
        if any(k in b for k in ("카페", "디저트", "커피")):
            return "#fdf4ff", "#a855f7"   # 보라 계열 — 카페
        if any(k in b for k in ("체크인", "체크아웃", "숙소")):
            return "#f0fdf4", "#16a34a"   # 초록 계열 — 숙소
        return "#f9fafb", "#6b7280"       # 회색 — 기타

    if range_m:
        t_start = range_m.group(1)
        t_end   = range_m.group(2)
        body    = range_m.group(3)
        bg, accent = _activity_style(body)
        st.markdown(
            f"<div style='margin:5px 0;padding:7px 10px;border-radius:8px;"
            f"background:{bg};border-left:4px solid {accent};"
            f"display:flex;align-items:baseline;gap:8px;'>"
            f"<span style='color:{accent};font-weight:700;white-space:nowrap;font-size:0.9rem;'>"
            f"{t_start}~{t_end}</span>"
            f"<span style='color:#1f2937;'>{body}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    elif single_m:
        t    = single_m.group(1)
        desc = single_m.group(2)
        is_event = any(kw in desc for kw in _EVENT_KW)
        if is_event:
            # 도착·출발·귀환 등 단일 시각 이벤트 — 컴팩트한 회색 스타일
            st.markdown(
                f"<div style='margin:3px 0 3px 4px;display:flex;align-items:center;gap:8px;'>"
                f"<span style='color:#6b7280;font-weight:600;font-size:0.85rem;white-space:nowrap;'>{t}</span>"
                f"<span style='color:#374151;font-size:0.88rem;'>{desc}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<div style='margin:6px 0;'>"
                f"<span style='color:#2563eb;font-weight:700;margin-right:8px;'>{t}</span>"
                f"{desc}</div>",
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            f"<div style='margin:4px 0;color:#374151;'>{line}</div>",
            unsafe_allow_html=True,
        )


def _render_itinerary_tabs(itinerary: list[str], result: dict):
    coord_lookup = _build_coord_lookup(result)

    tab_names, day_data = [], []
    for day_str in itinerary:
        lines  = day_str.strip().split("\n")
        header = lines[0]
        day_m  = re.search(r'\[(\d+일차)\s*\|\s*(\d{4}-(\d{2})-(\d{2}))', header)
        if day_m:
            tab_label = f"{day_m.group(1)}  {day_m.group(3)}/{day_m.group(4)}"
        else:
            tab_label = header
        tab_names.append(tab_label)
        day_data.append((header, lines[1:]))

    tabs = st.tabs(tab_names)

    for tab, (header, schedule_lines) in zip(tabs, day_data):
        with tab:
            col_sched, col_map = st.columns([1, 1], gap="large")

            with col_sched:
                st.markdown(
                    f"<h4 style='margin-bottom:12px;color:#1e3a5f;'>{header}</h4>",
                    unsafe_allow_html=True,
                )
                for line in schedule_lines:
                    if line.strip():
                        _render_schedule_line(line.strip())

            with col_map:
                locations = _extract_day_locations(schedule_lines, coord_lookup)

                # 숙소 핀 추가
                acc_idx_set: set[int] = set()
                acc_list = result.get("accommodations") or []
                if acc_list:
                    acc       = acc_list[0]
                    acc_title = acc.get("title", "")
                    # ① coord_lookup → ② dict 직접 파싱 → ③ Naver 검색 순으로 fallback
                    acc_coord = (
                        coord_lookup.get(acc_title)
                        or _parse_coord(acc)
                        or _naver_geocode(acc_title)
                        or _naver_geocode(acc.get("address", ""))
                    )
                    if acc_coord and acc_title:
                        existing_idx = next(
                            (i for i, (n, _) in enumerate(locations) if n == acc_title), None
                        )
                        if existing_idx is None:
                            locations = [(acc_title, acc_coord)] + locations
                            acc_idx_set = {0}
                        else:
                            acc_idx_set = {existing_idx}

                map_html = _make_naver_map_html(locations, acc_indices=acc_idx_set)
                components.html(map_html, height=520)

                if locations:
                    parts = []
                    for i, (name, _) in enumerate(locations):
                        if i in acc_idx_set:
                            parts.append(f"🏨 **{name}**")
                        else:
                            parts.append(f"**{i+1}.** {name}")
                    st.caption("  ".join(parts))


# ── 경비 렌더링 ──────────────────────────────────────────────────────

def _render_cost(cost: dict, num_people: int):
    st.subheader(f"💰 예상 총 경비  ({num_people}명 합산)")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("교통비 (왕복)",  f"{cost.get('transportation', 0):,}원")
    c2.metric("숙박비 (객실)",  f"{cost.get('accommodation',  0):,}원")
    c3.metric("식비",           f"{cost.get('meals',          0):,}원")
    c4.metric("관광·입장료",    f"{cost.get('activities',     0):,}원")

    total      = cost.get("total", 0)
    per_person = total // num_people if num_people > 1 else 0
    delta_lbl  = f"1인당 {per_person:,}원" if num_people > 1 else ""
    st.metric("합계", f"{total:,}원", delta=delta_lbl, delta_color="off")

    budget_total = cost.get("budget", 0)
    if budget_total > 0 and total > budget_total:
        over = total - budget_total
        st.error(
            f"설정 예산 {budget_total:,}원을 **{over:,}원 초과**합니다. "
            f"숙소 등급 조정 또는 관광지·식당 수 축소를 권장합니다.",
            icon="⚠️",
        )
    elif budget_total > 0:
        st.success(f"예산 {budget_total:,}원 이내입니다.", icon="✅")


# ── 최종 결과 ────────────────────────────────────────────────────────

def _render_final_plan(result: dict):
    st.divider()
    st.header(f"✈️  {result.get('plan_title', '여행 계획')}")

    st.subheader("📅 일자별 일정")
    _render_itinerary_tabs(result.get("itinerary") or [], result)

    cost = result.get("estimated_cost") or {}
    if cost:
        st.divider()
        _render_cost(cost, result.get("num_people") or 1)


# ── 사이드바 ─────────────────────────────────────────────────────────

def _sidebar(state: dict | None):
    with st.sidebar:
        st.header("📋 수집된 정보")

        if not state:
            st.caption("아직 입력된 정보가 없습니다.")
        else:
            for label, val in [
                ("출발지",       state.get("origin_city")),
                ("목적지",       state.get("city")),
                ("상세 위치",    state.get("district")),
                ("여행 기간",    state.get("traveldates")),
                ("인원",         f"{state.get('num_people')}명"  if state.get("num_people") else None),
                ("예산 (1인당)", f"{state.get('budget'):,}원"    if state.get("budget")     else None),
            ]:
                if val:
                    st.write(f"**{label}:** {val}")

            if prefs := state.get("preferences"):
                st.write(f"**선호 스타일:** {', '.join(prefs)}")

            if must := state.get("must_visit"):
                st.write("**필수 방문:**")
                for p in must:
                    st.write(f"  ⭐ {p}")

        st.divider()
        if st.button("🔄 처음부터 다시 시작", use_container_width=True):
            st.session_state.update(
                chat_history=[], graph_state=None, phase="start", result=None
            )
            st.rerun()


# ── 메인 ─────────────────────────────────────────────────────────────

def main():
    _init_session()
    graph = get_graph()

    _sidebar(st.session_state.graph_state)

    st.title("✈️ tripto")
    st.caption("여행지, 일정, 인원, 예산을 자유롭게 말씀해 주세요. AI가 최적의 여행 계획을 만들어 드립니다.")

    if st.session_state.phase == "start":
        with st.chat_message("assistant"):
            st.write(
                "안녕하세요! tripto입니다. 😊\n\n"
                "어디로, 언제, 몇 명이서 여행하고 싶으신지 편하게 말씀해 주세요. "
                "예산이나 꼭 가고 싶은 장소도 함께 알려주시면 더 좋아요!"
            )

    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    if st.session_state.phase == "done" and st.session_state.result:
        _render_final_plan(st.session_state.result)

    if st.session_state.phase != "done":
        user_input = st.chat_input("메시지를 입력하세요...")

        if user_input:
            st.session_state.chat_history.append({"role": "user", "content": user_input})

            prev_state  = st.session_state.graph_state
            graph_state = (
                {"question": user_input, "messages": [], "preferences": [],
                 "itinerary": [], "current_step": "start", "last_asked_field": None}
                if prev_state is None
                else {**prev_state, "question": user_input}
            )

            spinner_msg = (
                "주어진 정보로 관광지 검색 및 일정 최적화 중입니다. 1~2분 소요될 수 있어요..."
                if prev_state
                   and prev_state.get("current_step") == "awaiting_confirmation"
                   and _is_confirmation(user_input)
                else "분석 중..."
            )

            with st.spinner(spinner_msg):
                result = graph.invoke(graph_state)

            st.session_state.graph_state = result

            if result.get("current_step") == "optimized":
                st.session_state.phase  = "done"
                st.session_state.result = result
            else:
                messages = result.get("messages", [])
                bot_msg  = next(
                    (m.content for m in reversed(messages) if isinstance(m, AIMessage)),
                    None,
                )
                if bot_msg:
                    st.session_state.chat_history.append({"role": "assistant", "content": bot_msg})
                st.session_state.phase = "chatting"

            st.rerun()


if __name__ == "__main__":
    main()
