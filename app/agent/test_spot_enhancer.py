import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "nodes"))

from nodes.Spot_Enhancer import Spot_Enhancer

# test_state = {
#     "city": "부산",
#     "district": "해운대",
#     "preferences": ["야경", "해변"],
#     "tourist_spots": [],
#     "must_visit": ["광안대교"],
# }

test_state = {
    "city": "김포",
    "district": "",
    "preferences": [""],
    "tourist_spots": [],
    "must_visit": [""],
}



print("=== Spot_Enhancer 테스트 시작 ===\n")
result = Spot_Enhancer(test_state)
spots = result.get("tourist_spots", [])

print(f"총 {len(spots)}개 장소 반환\n")

coord_ok  = [s for s in spots if s.get("mapx") and s.get("mapy")]
coord_fail = [s for s in spots if not s.get("mapx") or not s.get("mapy")]

print(f"✅ 좌표 성공: {len(coord_ok)}개")
for s in coord_ok:
    src = "[LLM]" if s.get("llm_suggested") else "[API]"
    mv  = " ★필수" if s.get("must_visit") else ""
    print(f"  {src}{mv} {s['title']} | mapx={s['mapx']}, mapy={s['mapy']}")

print(f"\n❌ 좌표 실패: {len(coord_fail)}개")
for s in coord_fail:
    src = "[LLM]" if s.get("llm_suggested") else "[API]"
    mv  = " ★필수" if s.get("must_visit") else ""
    print(f"  {src}{mv} {s['title']} | address={s.get('address', '')}")
