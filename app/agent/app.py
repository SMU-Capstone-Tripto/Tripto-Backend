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


_CONFIRM_WORDS = {
    "네", "예", "맞아", "맞아요", "확인", "ok", "오케이",
    "좋아", "진행", "맞습니다", "맞음", "ㅇㅇ", "응", "그래",
    "correct", "yes", "괜찮아", "괜찮습니다", "오키",
}


def _is_confirmation(text: str) -> bool:
    tokens = set(re.split(r"[\s,!?.~]+", text.strip().lower()))
    return bool(tokens & _CONFIRM_WORDS)


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
    for key in ("tourist_spots", "restaurants", "cafes"):
        for item in (result.get(key) or []):
            title = item.get("title", "")
            coord = _parse_coord(item)
            if title and coord:
                lookup[title] = coord
    # 숙소는 selected_acc만 지도에 표시
    selected_acc = result.get("selected_acc")
    if selected_acc:
        coord = _parse_coord(selected_acc)
        title = selected_acc.get("title", "")
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


# ── 일정 스케줄 HTML 빌드 ────────────────────────────────────────────

def _activity_meta(body: str) -> tuple[str, str]:
    """(background, accent) 반환"""
    if any(k in body for k in ("관광", "방문", "탐방", "투어", "체험")):
        return "#eff6ff", "#2563eb"
    if any(k in body for k in ("식사", "아침", "점심", "저녁", "브런치", "맛집")):
        return "#fff7ed", "#f97316"
    if any(k in body for k in ("카페", "디저트", "커피")):
        return "#fdf4ff", "#a855f7"
    if any(k in body for k in ("체크인", "체크아웃", "숙소", "귀환")):
        return "#f0fdf4", "#16a34a"
    return "#f9fafb", "#6b7280"


_EVENT_KW = ("도착", "출발", "귀환", "체크인", "체크아웃")


def _build_schedule_html(schedule_lines: list[str]) -> str:
    """하루 일정을 타임라인 HTML 단일 블록으로 빌드 (중복 제거 포함)"""
    seen: set[str] = set()
    items: list[str] = []

    for raw in schedule_lines:
        line = raw.strip()
        if not line:
            continue
        # 공백 정규화 후 중복 제거
        key = re.sub(r"\s+", " ", line)
        if key in seen:
            continue
        seen.add(key)

        # ── 이동선 (→ 포함, 숫자 미시작) ──────────────────────────────
        if "→" in line and not line[0].isdigit():
            m = re.match(r"^(.+?)\s*\((.+)\)\s*$", line)
            if m:
                route  = m.group(1).strip()
                detail = m.group(2).strip()
                items.append(
                    f'<div style="display:flex;align-items:flex-start;gap:6px;'
                    f'margin:2px 0 6px 6px;">'
                    f'<span style="color:#d1d5db;font-size:0.75rem;margin-top:3px;'
                    f'flex-shrink:0;">↳</span>'
                    f'<span style="font-size:0.8rem;">'
                    f'<span style="color:#4b5563;">{route}</span>'
                    f'<span style="color:#9ca3af;margin-left:4px;">({detail})</span>'
                    f'</span></div>'
                )
            else:
                items.append(
                    f'<div style="font-size:0.8rem;color:#9ca3af;'
                    f'margin:2px 0 6px 12px;">↳ {line}</div>'
                )
            continue

        # ── 범위 활동 HH:MM~HH:MM ───────────────────────────────────
        range_m = re.match(r"^(\d{2}:\d{2})~(\d{2}:\d{2})\s+(.*)", line)
        if range_m:
            t_s, t_e, body = range_m.group(1), range_m.group(2), range_m.group(3)
            bg, accent = _activity_meta(body)
            items.append(
                f'<div style="position:relative;margin:6px 0;">'
                f'<div style="position:absolute;left:-21px;top:50%;transform:translateY(-50%);'
                f'width:10px;height:10px;border-radius:50%;background:{accent};'
                f'border:2px solid white;box-shadow:0 0 0 2px {accent};"></div>'
                f'<div style="background:{bg};border-radius:8px;padding:8px 12px;'
                f'border-left:4px solid {accent};display:flex;align-items:baseline;gap:8px;">'
                f'<span style="color:{accent};font-weight:700;font-size:0.85rem;'
                f'white-space:nowrap;">{t_s}~{t_e}</span>'
                f'<span style="color:#1f2937;">{body}</span>'
                f'</div></div>'
            )
            continue

        # ── 단일 시각 HH:MM ─────────────────────────────────────────
        single_m = re.match(r"^(\d{2}:\d{2})\s+(.*)", line)
        if single_m:
            t, desc = single_m.group(1), single_m.group(2)
            is_ev   = any(kw in desc for kw in _EVENT_KW)
            dot_col = "#9ca3af" if is_ev else "#2563eb"
            badge_bg = "#f3f4f6" if is_ev else "#dbeafe"
            badge_fg = "#374151" if is_ev else "#1d4ed8"
            items.append(
                f'<div style="position:relative;margin:5px 0;">'
                f'<div style="position:absolute;left:-19px;top:50%;transform:translateY(-50%);'
                f'width:8px;height:8px;border-radius:50%;background:{dot_col};'
                f'border:2px solid white;"></div>'
                f'<div style="display:flex;align-items:center;gap:8px;'
                f'padding:4px 10px;background:#f9fafb;border-radius:6px;">'
                f'<span style="background:{badge_bg};border-radius:4px;padding:1px 7px;'
                f'font-size:0.78rem;font-weight:700;color:{badge_fg};'
                f'white-space:nowrap;">{t}</span>'
                f'<span style="color:#374151;font-size:0.88rem;">{desc}</span>'
                f'</div></div>'
            )
            continue

        # ── 기타 텍스트 ─────────────────────────────────────────────
        items.append(
            f'<div style="margin:3px 0;color:#6b7280;font-size:0.85rem;'
            f'padding-left:4px;">{line}</div>'
        )

    inner = "\n".join(items)
    return (
        f'<div style="position:relative;padding-left:26px;padding-top:4px;">'
        f'<div style="position:absolute;left:7px;top:8px;bottom:8px;width:2px;'
        f'background:#e5e7eb;border-radius:2px;"></div>'
        f'{inner}'
        f'</div>'
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
                st.markdown(_build_schedule_html(schedule_lines), unsafe_allow_html=True)

            with col_map:
                locations = _extract_day_locations(schedule_lines, coord_lookup)

                acc_idx_set: set[int] = set()
                acc = result.get("selected_acc") or (result.get("accommodations") or [None])[0]
                if acc:
                    acc_title = acc.get("title", "")
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
    total  = cost.get("total", 0)
    budget = cost.get("budget", 0)

    categories = [
        ("교통비",      "transportation", "#2563eb"),
        ("숙박비",      "accommodation",  "#16a34a"),
        ("식비",        "meals",          "#f97316"),
        ("관광·입장료", "activities",     "#9333ea"),
    ]

    # 컬러 스택 바 (flex 비율로 자동 분배)
    segs = "".join(
        f'<div style="flex:{cost.get(key,0)};background:{color};min-width:2px;" '
        f'title="{label}: {cost.get(key,0):,}원"></div>'
        for label, key, color in categories
        if cost.get(key, 0) > 0
    )
    st.markdown(
        f'<div style="display:flex;border-radius:8px;overflow:hidden;'
        f'height:22px;margin:4px 0 14px;">{segs}</div>',
        unsafe_allow_html=True,
    )

    # 범례 + 금액 + 비율
    cols = st.columns(4)
    for i, (label, key, color) in enumerate(categories):
        val = cost.get(key, 0)
        pct = round(val / total * 100) if total > 0 else 0
        cols[i].markdown(
            f'<div style="display:flex;align-items:center;gap:5px;margin-bottom:2px;">'
            f'<div style="width:10px;height:10px;border-radius:2px;background:{color};flex-shrink:0;"></div>'
            f'<span style="font-size:0.78rem;color:#6b7280;">{label}</span></div>'
            f'<div style="font-size:0.9rem;font-weight:700;color:#111827;">{val:,}원</div>'
            f'<div style="font-size:0.75rem;color:#9ca3af;">{pct}%</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)

    per_person = total // num_people if num_people > 1 else 0
    delta_lbl  = f"1인당 {per_person:,}원" if num_people > 1 else ""
    st.metric(f"합계 ({num_people}명)", f"{total:,}원", delta=delta_lbl, delta_color="off")

    if budget > 0 and total > budget:
        st.error(
            f"설정 예산 {budget:,}원을 **{total - budget:,}원 초과**합니다. "
            f"숙소 등급 조정 또는 관광지·식당 수 축소를 권장합니다.",
            icon="⚠️",
        )
    elif budget > 0:
        st.success(f"예산 {budget:,}원 이내입니다.", icon="✅")


# ── 숙소 카드 ────────────────────────────────────────────────────────

def _render_accommodation_card(result: dict):
    selected_acc     = result.get("selected_acc")
    room_combination = result.get("room_combination") or []
    num_people       = result.get("num_people") or 1

    if not selected_acc:
        return

    # 박수 계산
    try:
        start_str, end_str = result.get("traveldates", "").split("~")
        from datetime import datetime as _dt
        num_nights = (_dt.strptime(end_str.strip(), "%Y-%m-%d") -
                      _dt.strptime(start_str.strip(), "%Y-%m-%d")).days
    except Exception:
        num_nights = 1

    title   = selected_acc.get("title", "숙소")
    address = selected_acc.get("address", "")
    tel     = selected_acc.get("tel", "")
    image   = selected_acc.get("firstimage") or selected_acc.get("image", "")

    with st.expander(f"🏨 숙소 안내 — {title}", expanded=False):
        col_img, col_info = st.columns([1, 2], gap="large")

        with col_img:
            if image:
                st.image(image, use_container_width=True)
            else:
                st.markdown(
                    "<div style='background:#f3f4f6;border-radius:8px;height:140px;"
                    "display:flex;align-items:center;justify-content:center;"
                    "color:#9ca3af;font-size:2rem;'>🏨</div>",
                    unsafe_allow_html=True,
                )

        with col_info:
            st.markdown(f"**{title}**")
            if address:
                st.markdown(f"📍 {address}")
            if tel:
                st.markdown(f"📞 {tel}")

        if room_combination:
            st.divider()
            st.markdown(f"**추천 방 구성** ({num_people}명 기준)")

            total_per_night = 0
            for item in room_combination:
                room      = item["room"]
                count     = item["count"]
                price     = item["price_per_night"]
                subtotal  = price * count
                total_per_night += subtotal
                cap       = room.get("max_capacity", 0)
                base      = room.get("base_capacity", 0)
                st.markdown(
                    f"- **{room.get('room_name', '객실')}** × {count}개 &nbsp;"
                    f"<span style='color:#6b7280;font-size:0.85rem;'>"
                    f"(기준 {base}인 / 최대 {cap}인 | {price:,}원/박)</span> "
                    f"→ **{subtotal:,}원/박**",
                    unsafe_allow_html=True,
                )

            st.markdown(
                f"<div style='margin-top:8px;padding:10px 14px;background:#eff6ff;"
                f"border-radius:8px;border-left:4px solid #2563eb;'>"
                f"<span style='font-size:0.85rem;color:#6b7280;'>1박 합계</span><br>"
                f"<span style='font-size:1.1rem;font-weight:700;color:#1d4ed8;'>"
                f"{total_per_night:,}원</span>"
                f"<span style='color:#9ca3af;font-size:0.85rem;margin-left:10px;'>"
                f"× {num_nights}박 = {total_per_night * num_nights:,}원</span>"
                f"</div>",
                unsafe_allow_html=True,
            )


# ── 최종 결과 ────────────────────────────────────────────────────────

def _render_final_plan(result: dict):
    st.subheader(f"✈️ {result.get('plan_title', '여행 계획')}")

    st.caption("📅 일자별 일정")
    _render_itinerary_tabs(result.get("itinerary") or [], result)

    _render_accommodation_card(result)

    cost = result.get("estimated_cost") or {}
    if cost:
        st.divider()
        st.caption("💰 예상 경비")
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

    col_chat, col_plan = st.columns([2, 3], gap="large")

    # ── 왼쪽: 채팅 영역 ─────────────────────────────────────────────
    with col_chat:
        st.subheader("💬 대화")

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

        if st.session_state.phase == "completed":
            st.success("🔒 최종 여행 일정 수립이 완료되었습니다. 다시 설계하고 싶으시면 왼쪽의 '처음부터 다시 시작' 버튼을 눌러주세요.")

    # ── 오른쪽: 일정 + 경비 영역 ────────────────────────────────────
    with col_plan:
        if st.session_state.result:
            _render_final_plan(st.session_state.result)
        else:
            st.markdown(
                "<div style='text-align:center;color:#9ca3af;margin-top:80px;'>"
                "<div style='font-size:3rem;'>✈️</div>"
                "<div style='margin-top:12px;font-size:0.95rem;line-height:1.6;'>"
                "여행 정보를 입력하시면<br>AI가 최적의 일정을 생성해 드립니다.</div>"
                "</div>",
                unsafe_allow_html=True,
            )

    # ── 채팅 입력 (페이지 하단 고정) ────────────────────────────────
    if st.session_state.phase != "completed":
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
            current_step = result.get("current_step")

            if current_step == "optimized":
                st.session_state.phase  = "chatting"
                st.session_state.result = result
                # Revision_Manager가 남긴 완료 메시지가 있으면 채팅에 추가
                messages = result.get("messages", [])
                bot_msg  = next(
                    (m.content for m in reversed(messages) if isinstance(m, AIMessage)),
                    None,
                )
                if bot_msg:
                    st.session_state.chat_history.append({"role": "assistant", "content": bot_msg})
            elif current_step == "completed":
                st.session_state.phase  = "completed"
                st.session_state.chat_history.append({"role": "assistant", "content": "🎉 좋은 여행 계획이 완성되었습니다! 즐거운 여행 되세요."})
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