# -*- coding: utf-8 -*-
"""
HealF (헬스 프렌드) - 헬스장 기구 혼잡도 기반 '예약 + 루틴 추천' 데모 앱 (목업)

실행 방법
    pip install flet
    python healf.py

- 단일 파일 / 백엔드·DB 없음 (모든 데이터는 메모리상의 더미 데이터)
- 알림 대기시간은 발표 시연을 위해 수 초 단위로 가속되어 있습니다.
- 회원 예약은 최대 MAX_RESERVATIONS(3)개. '사용 가능' 알림이 오면 READY_TIMEOUT_SEC(90초) 안에
  [사용 / 미루기 / 취소] 중 하나를 선택해야 하며, 무응답이면 자동 취소됩니다.
- 기구는 한 번에 하나만 사용할 수 있고, 1회 최대 이용시간은 MAX_USE_SEC(30분)이며 넘으면 자동 종료됩니다.
- Flet 버전에 따라 달라지는 API(탭, 다이얼로그, 스낵바, 앱 실행)는 호환 처리해 두었습니다.
"""

import math
import random
import re
import threading
import time
import zlib
from datetime import datetime, timedelta

import flet as ft

# ======================================================================
# 1. 디자인 토큰  (Black + Red)
# ======================================================================
BG = "#0E0E11"          # 순수 검정(#000) 대신 아주 짙은 회색: 글자 번짐·눈부심 감소
CARD = "#18181D"        # 배경과 구분되도록 카드를 조금 더 밝게
CARD_HI = "#212127"
BORDER = "#30303A"
RED = "#E11D2E"         # 브랜드·버튼·혼잡 상태 전용
RED_SOFT = "#3A0F14"
WINE = "#4A1218"        # 부위 태그 배경 (어두운 와인색)
WINE_TEXT = "#E8B4B9"   # 부위 태그 글자 (연한 분홍빛)
TEXT = "#ECECEF"
MUTED = "#B5B5BD"       # 보조 글자를 더 밝게
ICON_MUTED = "#8A8A94"  # 장식용 아이콘 (빨강 남용 줄이기)
BAR_NEUTRAL = "#6B6B78" # 상태와 무관한 막대 (이용 회원 수 등)
GREEN = "#2ECC71"
GREEN_SOFT = "#0F2E1D"
AMBER = "#F5A623"
AMBER_SOFT = "#33260A"
WHITE = "#FFFFFF"

BOLD = ft.FontWeight.BOLD
CENTER = ft.CrossAxisAlignment.CENTER
BETWEEN = ft.MainAxisAlignment.SPACE_BETWEEN

# ======================================================================
# 2. 시연용 설정값 / 더미 데이터
# ======================================================================
PARTS = ["등", "가슴", "이두", "삼두", "어깨", "하체", "전완"]
PART_ORDER = ["하체", "등", "가슴", "어깨", "이두", "삼두", "전완"]  # 루틴 순서(큰 근육 -> 작은 근육)
BIG_PARTS = {"하체", "등", "가슴", "어깨"}

DEMO_BASE_SEC = 4           # 알림까지 기본 대기(초)
DEMO_PER_PERSON_SEC = 2     # 내 앞 대기 1명당 추가(초)
DEMO_MAX_SEC = 12           # 최대 대기(초)
MIN_PER_PERSON = 3          # (예비값) 부위 정보가 없을 때 1명당 대기 분

# 예상 대기 시간 = (대기 인원 ÷ 기구 대수) × 그 기구의 '1회 평균 이용시간'
# 1회 평균 이용시간은 회원들의 실제 사용 기록([사용] ~ [사용 완료])으로 계속 갱신된다.
# 기록이 적은 운영 초기에는 아래 부위별 기본값을 함께 섞어 쓴다 (기본값을 USE_PRIOR_WEIGHT 회분 기록으로 취급).
DEFAULT_USE_MIN = {"하체": 8, "등": 7, "가슴": 7, "어깨": 6, "이두": 5, "삼두": 5, "전완": 4}
USE_PRIOR_WEIGHT = 5        # 기본값의 비중 (기록 5회분)
USE_HISTORY_N = 20          # 기구별로 최근 20회 사용 기록만 반영
USE_MIN_CLAMP = (1, 30)     # 기록 1건은 1~30분 범위로 보정 (시연에서 몇 초만 쓰고 끝내도 1분으로 기록)

MAX_RESERVATIONS = 3        # 회원 1명이 동시에 가질 수 있는 예약 수 (대기 + 사용 가능 + 사용 중)
READY_TIMEOUT_SEC = 90      # '사용 가능' 알림 후 응답 제한시간 = 1분 30초 (시연 때 줄이려면 숫자만 바꾸세요)
MAX_USE_SEC = 30 * 60       # 기구 1회 최대 이용시간 = 30분, 넘으면 자동 사용 종료 (시연 때 줄이려면 숫자만 바꾸세요)

# (시간대, 이용 회원, 평균 대기(분), 최다 이용 기구, HealF 추천으로 분산된 이용 건수)
TREND = [
    ("06~08시", 38, 1.2, "레그 컬", 5),
    ("08~10시", 55, 2.0, "랫풀다운", 9),
    ("10~12시", 61, 2.4, "체스트 프레스", 12),
    ("12~14시", 84, 4.5, "레그 프레스", 21),
    ("14~16시", 49, 1.8, "시티드 로우", 11),
    ("16~18시", 97, 6.1, "체스트 프레스", 24),
    ("18~20시", 158, 11.8, "레그 프레스", 47),
    ("20~22시", 126, 8.3, "랫풀다운", 33),
]

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def seed_equipments():
    # (이름, 부위, 수량, 타인 사용 중, 타인 대기)  - 대기는 모든 기구가 찼을 때만 존재
    rows = [
        ("체스트 프레스", "가슴", 4, 4, 3),
        ("인클라인 벤치프레스", "가슴", 2, 2, 2),
        ("펙덱 플라이", "가슴", 2, 1, 0),
        ("스미스 머신 벤치", "가슴", 2, 1, 0),
        ("랫풀다운", "등", 3, 3, 2),
        ("시티드 로우", "등", 3, 1, 0),
        ("어시스티드 풀업", "등", 1, 1, 1),
        ("백 익스텐션", "등", 2, 0, 0),
        ("숄더 프레스", "어깨", 3, 2, 0),
        ("사이드 레터럴 레이즈", "어깨", 2, 0, 0),
        ("레그 프레스", "하체", 3, 3, 4),
        ("레그 익스텐션", "하체", 3, 1, 0),
        ("레그 컬", "하체", 2, 2, 1),
        ("힙 어브덕션", "하체", 3, 1, 0),
        ("바이셉 컬 머신", "이두", 2, 1, 0),
        ("프리처 컬", "이두", 2, 2, 1),
        ("케이블 푸시다운", "삼두", 3, 3, 2),
        ("트라이셉스 익스텐션", "삼두", 2, 0, 0),
        ("딥스 머신", "삼두", 2, 1, 0),
        ("리스트 컬 벤치", "전완", 2, 0, 0),
    ]
    return [
        {"id": i + 1, "name": n, "part": p, "qty": q, "others_occ": o, "others_wait": w, "use_log": []}
        for i, (n, p, q, o, w) in enumerate(rows)
    ]


S = {}   # 앱 전체 상태 (메모리)
lock = threading.RLock()


CARE_CATS = [   # (요청 종류, 아이콘) - 이탈 사유가 될 수 있지만 관장이 바로 케어할 수 있는 것들
    ("음악 소리가 너무 커요", "VOLUME_UP"),
    ("기구 청소가 필요해요", "CLEANING_SERVICES"),
    ("기구 고장·점검이 필요해요", "BUILD"),
    ("실내가 너무 더워요", "WB_SUNNY"),
    ("실내가 너무 추워요", "AC_UNIT"),
    ("비매너 이용자가 있어요", "DO_NOT_DISTURB_ON"),
    ("수건·소모품이 부족해요", "INVENTORY_2"),
    ("탈의실·샤워실 청결", "SHOWER"),
    ("기타", "CHAT_BUBBLE_OUTLINE"),
]
CARE_ICON = dict(CARE_CATS)
CARE_WEEK_BASE = {"기구 청소가 필요해요": 14, "실내가 너무 더워요": 10, "음악 소리가 너무 커요": 9,
                  "비매너 이용자가 있어요": 7, "수건·소모품이 부족해요": 6, "기구 고장·점검이 필요해요": 4,
                  "탈의실·샤워실 청결": 4, "실내가 너무 추워요": 3, "기타": 2}   # 요청 종류별 한 주 평균 건수 (더미)
CARE_TIP = {   # 분석 페이지에서 보여줄 관장용 대응 팁
    "기구 청소가 필요해요": "피크 전(17시) 기구 소독 순회를 1회 추가하고, 기구 옆 소독 티슈 비치를 점검해 보세요.",
    "실내가 너무 더워요": "피크 시간대 냉방 온도를 1~2도 낮추거나 유산소 존 선풍기 추가를 검토해 보세요.",
    "실내가 너무 추워요": "에어컨 바람이 직접 닿는 자리(출입구·스트레칭 존)를 확인해 보세요.",
    "음악 소리가 너무 커요": "프리웨이트 존 스피커 음량을 시간대별로 다르게 설정해 보세요.",
    "비매너 이용자가 있어요": "기구 자리 맡기·장시간 독점 금지 안내문을 붙이고, 1회 이용시간 제한을 공지해 보세요.",
    "수건·소모품이 부족해요": "저녁 피크 전에 수건·소모품 보충 시간을 정해 두세요.",
    "기구 고장·점검이 필요해요": "점검이 필요한 기구는 앱에서 '점검 중'으로 표시해 예약을 막아 두세요.",
    "탈의실·샤워실 청결": "탈의실 청소 주기를 피크 시간 직후로 옮겨 보세요.",
    "기타": "직접 내용을 읽고 답변을 남겨 주세요.",
}


def care_week_counts(days, care_items):
    """요청 종류별 최근 7일 일별 건수. 지난 6일은 더미, 오늘은 실제 요청함 기준."""
    today = days[-1]
    out = {}
    for cat, _ in CARE_CATS:
        mean = CARE_WEEK_BASE.get(cat, 2) / 7
        row = []
        for d in days[:-1]:
            rng = random.Random(f"care|{cat}|{d.isoformat()}")
            row.append(int(round(rng.uniform(0.3, 1.7) * mean)))
        row.append(sum(1 for x in care_items if x["cat"] == cat and x["at"].date() == today))
        out[cat] = row
    return out


def reset_state():
    eqs = seed_equipments()
    S["equipments"] = eqs
    S["tickets"] = []
    S["next_eid"] = len(eqs) + 1
    S["next_tid"] = 1
    S["reservations"] = 128      # 오늘 HealF 예약 건수(더미 시작값)
    S["completed"] = 109         # 오늘 HealF 예약 중 실제 운동(사용 완료)까지 끝난 건수 (더미 시작값)
    S["floor"] = 21              # 기구를 쓰지 않고 유산소·스트레칭·휴식 중인 고객 수 (더미)
    S["mpart"] = "전체"           # 회원용 탭에서 선택한 부위
    S["gym"] = {"open": "06:00", "close": "23:00"}
    S["tab"] = 0
    # 수익 계산 설정 (관장이 직접 입력)
    S["biz"] = {
        "arpu": 60000,        # 회원 1명당 월평균 회원권 매출(원) = 이번 달 회원권 총매출 ÷ 활성 회원 수
        "price": 5000000,     # 기구 1대 가격(원)
        "churn": 3,           # 대기 불만으로 재등록하지 않는 비율(%) - 가정값 (보수적으로)
    }
    S["long_wait"] = {}       # 오늘 '대기가 너무 길어요' 누적 {기구 id: 건수} (다른 회원 더미는 long_wait_week에 포함)
    S["my_long_wait"] = None  # (날짜, 기구 id) - 회원 1명은 하루 한 번만 보낼 수 있다
    # 회원 케어 요청 (다른 회원들의 더미 요청 + 시연 중 내가 보낸 요청)
    now = datetime.now()
    midnight = datetime(now.year, now.month, now.day)
    S["care"] = [
        {"id": i + 1, "cat": c, "detail": d, "at": max(now - timedelta(minutes=m), midnight + timedelta(seconds=i)),
         "status": st, "mine": False, "reply": rp}
        for i, (c, d, m, st, rp) in enumerate([
            ("기구 청소가 필요해요", "레그 프레스 2번 시트에 땀이 남아 있어요", 18, "접수", ""),
            ("음악 소리가 너무 커요", "프리웨이트 존 스피커", 42, "확인", "확인했습니다. 시간대별로 음량을 조절해 볼게요."),
            ("비매너 이용자가 있어요", "스미스 머신에 물건 올려두고 자리 맡기", 75, "접수", ""),
            ("수건·소모품이 부족해요", "남자 탈의실 수건", 130, "완료", "수건 채워 두었어요. 감사합니다!"),
            ("실내가 너무 더워요", "유산소 존", 210, "완료", "에어컨 온도를 2도 낮췄어요."),
        ])
    ]
    S["next_cid"] = len(S["care"]) + 1
    S["care_sel"] = CARE_CATS[0][0]


reset_state()


# ======================================================================
# 3. 혼잡도 / 대기열 로직 (UI와 무관한 순수 로직)
# ======================================================================
def get_eq(eid):
    return next((e for e in S["equipments"] if e["id"] == eid), None)


def my_count(eid, statuses):
    return sum(1 for t in S["tickets"] if t["eq_id"] == eid and t["status"] in statuses)


HOLD = ("ready", "using")   # 기구 1대를 실제로 잡고 있는 내 예약 상태 (사용 가능 알림 ~ 사용 중)


def fmt_dur(sec):
    m, s = divmod(int(sec), 60)
    return f"{m}분 {s}초" if m and s else (f"{m}분" if m else f"{s}초")


def occupied(eq):
    return min(eq["qty"], eq["others_occ"] + my_count(eq["id"], HOLD))


def free_units(eq):
    return max(0, eq["qty"] - occupied(eq))


def waiting_total(eq):
    return eq["others_wait"] + my_count(eq["id"], ("waiting", "timing"))


def congestion(eq):
    """0~100 혼잡도 점수: 점유율 70% + 대기 압력 30%"""
    qty = max(eq["qty"], 1)
    occ_ratio = occupied(eq) / qty
    wait_ratio = min(1.0, waiting_total(eq) / (qty * 1.5))
    return int(round(min(100, 70 * occ_ratio + 30 * wait_ratio)))


def level(score):
    if score < 40:
        return "여유", GREEN, GREEN_SOFT
    if score < 70:
        return "보통", AMBER, AMBER_SOFT
    return "혼잡", RED, RED_SOFT


def avg_use_min(eq):
    """기구 1회 평균 이용시간(분) = (부위 기본값 x 가중치 + 실제 기록 합) / (가중치 + 기록 수)"""
    log = eq.get("use_log", [])
    base = DEFAULT_USE_MIN.get(eq["part"], MIN_PER_PERSON)
    return (base * USE_PRIOR_WEIGHT + sum(log)) / (USE_PRIOR_WEIGHT + len(log))


def est_wait_min(eq, extra=0):
    """예상 대기(분) = (대기 인원 ÷ 대수) x 1회 평균 이용시간. 빈 기구가 있고 대기가 없으면 0분.
    extra: 내가 지금 줄을 선다면 앞에 추가로 있는 인원(보통 0)."""
    w = waiting_total(eq) + extra
    if free_units(eq) > 0 and w == 0:
        return 0
    return int(math.ceil(max(w, 1) / max(eq["qty"], 1) * avg_use_min(eq)))


def wait_label(eq):
    m = est_wait_min(eq)
    return "바로 사용 가능" if m == 0 else f"약 {m}분"


def wait_color(m):
    return GREEN if m == 0 else (AMBER if m < 10 else RED)


def record_use(eq, started_at, ended_at):
    """사용 완료 시 실제 이용시간을 기록해 다음 예상 대기 계산에 반영한다."""
    if not eq or not started_at:
        return
    lo, hi = USE_MIN_CLAMP
    mins = max(lo, min(hi, (ended_at - started_at) / 60))
    eq.setdefault("use_log", []).append(round(mins, 1))
    del eq["use_log"][:-USE_HISTORY_N]


def find_ticket(tid):
    return next((x for x in S["tickets"] if x["id"] == tid), None)


def release_unit(eq):
    """내가 잡고 있던 기구 1대를 놓았을 때, 대기 중이던 다음 회원에게 바로 인계한다.
    (호출 전에 해당 예약은 이미 HOLD 상태가 아니거나 목록에서 빠져 있어야 한다)"""
    if eq and eq["others_wait"] > 0 and free_units(eq) > 0:
        eq["others_wait"] -= 1
        eq["others_occ"] += 1
        return True
    return False


def start_next(eid):
    """같은 기구의 내 예약 중 '맨 앞' 한 건만 타이머를 시작한다 (FIFO)."""
    q = [t for t in S["tickets"] if t["eq_id"] == eid]
    if any(t["status"] in ("timing", "ready", "using") for t in q):
        return
    for t in q:
        if t["status"] == "waiting":
            eq = get_eq(eid)
            others = eq["others_wait"] if eq else 0
            ahead = others + t.get("penalty", 0)          # 미루기를 하면 최소 1명 뒤에서 시작
            delay = min(DEMO_MAX_SEC, DEMO_BASE_SEC + DEMO_PER_PERSON_SEC * ahead)
            t.update(status="timing", ahead=ahead, ahead_others=others, delay=delay,
                     ready_at=time.time() + delay)
            return


def fire(t):
    """앞 사용자가 끝났다고 가정 -> '사용 가능' 상태로 전환하고 응답 제한시간 카운트다운 시작"""
    eq = get_eq(t["eq_id"])
    if eq:
        eq["others_wait"] = max(0, eq["others_wait"] - t.get("ahead_others", 0))
        if eq["qty"] - eq["others_occ"] - my_count(eq["id"], HOLD) <= 0 and eq["others_occ"] > 0:
            eq["others_occ"] -= 1  # 앞 사용자 종료
    t.update(status="ready", penalty=0, popup_shown=False, expire_at=time.time() + READY_TIMEOUT_SEC)


def using_ticket():
    """지금 내가 사용 중인 기구(예약). 한 번에 한 기구만 사용할 수 있다."""
    return next((x for x in S["tickets"] if x["status"] == "using"), None)


def use_ticket(t):
    """[사용] - 사용 가능 상태의 예약을 실제 사용 시작으로 전환 (최대 MAX_USE_SEC 동안)"""
    now = time.time()
    t.update(status="using", started_at=now, end_at=now + MAX_USE_SEC)


def finish_ticket(t):
    """[사용 완료] / 최대 이용시간 경과 자동 종료 공통 처리. 대기 중이던 다음 회원에게 인계했는지 반환."""
    S["tickets"].remove(t)
    S["completed"] += 1
    record_use(get_eq(t["eq_id"]), t.get("started_at"), time.time())   # 실제 이용시간 기록
    handed = release_unit(get_eq(t["eq_id"]))
    start_next(t["eq_id"])
    return handed


def cancel_ticket(t):
    """[취소] / 자동 취소 공통 처리. 기구를 잡고 있던(사용 가능) 예약이면 다음 회원에게 인계한다."""
    S["tickets"].remove(t)
    handed = release_unit(get_eq(t["eq_id"])) if t["status"] == "ready" else False
    start_next(t["eq_id"])
    return handed


def postpone_ticket(t):
    """[미루기] - 같은 기구의 내 예약 중 맨 뒤로 보내고 다시 대기한다."""
    eid = t["eq_id"]
    eq = get_eq(eid)
    S["tickets"].remove(t)
    S["tickets"].append(t)                                # 예약 순위 맨 뒤로
    t.update(status="waiting", penalty=0, popup_shown=False, postponed=t.get("postponed", 0) + 1)
    handed = release_unit(eq)                             # 비워 준 기구는 다음 대기자에게
    if not handed and eq and free_units(eq) > 0:
        eq["others_occ"] += 1                             # 대기자가 없으면 다른 회원이 먼저 쓰기 시작했다고 가정
    t["penalty"] = 1                                      # 내 앞에 최소 1명
    start_next(eid)
    return handed


def do_reserve(eid):
    with lock:
        eq = get_eq(eid)
        if not eq:
            return None
        if len(S["tickets"]) >= MAX_RESERVATIONS:
            return {"error": "limit", "name": eq["name"]}
        t = {"id": S["next_tid"], "eq_id": eid, "status": "waiting",
             "ahead": 0, "ahead_others": 0, "delay": 0, "ready_at": 0, "started_at": 0,
             "expire_at": 0, "end_at": 0, "penalty": 0, "postponed": 0, "popup_shown": False}
        S["next_tid"] += 1
        S["tickets"].append(t)
        S["reservations"] += 1
        start_next(eid)
        return {"name": eq["name"], "ahead": eq["others_wait"], "status": t["status"], "est": est_wait_min(eq),
                "mine": my_count(eid, ("waiting", "timing", "ready", "using"))}


def simulate_refresh():
    """다른 회원들의 이용 상황이 바뀐 것처럼 값을 흔든다."""
    with lock:
        for eq in S["equipments"]:
            mine = my_count(eq["id"], HOLD)
            cap = max(0, eq["qty"] - mine)
            eq["others_occ"] = max(0, min(cap, eq["others_occ"] + random.choice([-1, 0, 0, 1])))
            eq["others_wait"] = max(0, min(6, eq["others_wait"] + random.choice([-1, 0, 1])))
            if eq["others_occ"] + mine < eq["qty"]:
                eq["others_wait"] = 0
        S["floor"] = max(5, min(40, S["floor"] + random.choice([-2, -1, 0, 1, 2])))


def present_customers():
    """현재 헬스장 안 고객 수 = 기구 이용 중 + 기구 대기 중 + 기구 외 운동/휴식 중"""
    return (sum(occupied(e) for e in S["equipments"]) + sum(waiting_total(e) for e in S["equipments"])
            + S.get("floor", 0))


# ======================================================================
# 3-2. 주간 혼잡도 리포트 (더미 데이터 + 증설/처분 추천 로직)
# ======================================================================
KO_DAY = "월화수목금토일"

# 기구별 '평소 혼잡도(%)' 기준값 (시연용 더미). 목록에 없는 기구(관장이 새로 등록한 기구)는 이름으로 고정값을 만든다.
WEEK_BASE = {
    "체스트 프레스": 90, "레그 프레스": 88, "인클라인 벤치프레스": 82, "랫풀다운": 76, "어시스티드 풀업": 80,
    "케이블 푸시다운": 60, "프리처 컬": 64, "레그 컬": 62, "숄더 프레스": 55, "펙덱 플라이": 52,
    "시티드 로우": 48, "바이셉 컬 머신": 46, "스미스 머신 벤치": 45, "레그 익스텐션": 42, "힙 어브덕션": 40,
    "딥스 머신": 30, "트라이셉스 익스텐션": 27, "사이드 레터럴 레이즈": 28, "백 익스텐션": 22, "리스트 컬 벤치": 15,
}
DAY_FACTOR = [1.08, 1.00, 1.03, 1.00, 0.96, 0.88, 0.78]                 # 월~일 전체 붐비는 정도
DAY_PART_BOOST = {                                                        # 요일별 인기 부위 (헬스장 분할 루틴 느낌)
    0: {"가슴": 1.15}, 1: {"등": 1.12}, 2: {"하체": 1.15}, 3: {"어깨": 1.12},
    4: {"이두": 1.10, "삼두": 1.10}, 5: {"가슴": 1.05, "하체": 1.05},
}
PEAK_WEEKDAY = ["07~09시", "12~14시", "18~20시", "18~20시", "20~22시"]
PEAK_WEEKEND = ["10~12시", "14~16시", "16~18시"]

# 추천 판단 기준
EXPAND_AVG = 70             # 주 평균 혼잡도가 이 값 이상이거나
EXPAND_DAYS = 4             # 혼잡(70%↑)한 날이 이 일수 이상이면 증설 추천
DISPOSE_AVG = 35            # 주 평균이 이 값 이하이고
DISPOSE_PEAK = 60           # 가장 붐빈 날도 이 값 이하이며
DISPOSE_AFTER_MAX = 65      # 1대 줄인 뒤 예상 혼잡도가 이 값 이하일 때만 처분(감축) 추천


def week_days(today):
    """오늘을 마지막으로 하는 최근 7일 (오래된 날 -> 오늘)"""
    return [today - timedelta(days=6 - i) for i in range(7)]


def day_congestion(eq, d):
    """기구 eq의 날짜 d 하루 평균 혼잡도(%) - 이름+날짜로 고정 시드를 써서 항상 같은 값이 나온다."""
    rng = random.Random(f"{eq['name']}|{d.isoformat()}")
    base = WEEK_BASE.get(eq["name"])
    if base is None:
        base = 25 + zlib.crc32(eq["name"].encode("utf-8")) % 50
    wd = d.weekday()
    v = base * DAY_FACTOR[wd] * DAY_PART_BOOST.get(wd, {}).get(eq["part"], 1.0) + rng.uniform(-5, 5)
    return int(max(5, min(100, round(v))))


def day_peak_slot(eq, d):
    rng = random.Random(f"peak|{eq['name']}|{d.isoformat()}")
    return rng.choice(PEAK_WEEKEND if d.weekday() >= 5 else PEAK_WEEKDAY)


def wait_minutes(score):
    return round(max(0, score - 40) / 6.0, 1)


def weekly_stats(eq, days):
    vals = [day_congestion(eq, d) for d in days]
    peak = max(vals)
    return {"vals": vals, "avg": int(round(sum(vals) / len(vals))), "peak": peak, "peak_day": days[vals.index(peak)],
            "over70": sum(1 for v in vals if v >= 70), "under40": sum(1 for v in vals if v < 40)}


def daily_usage(equipments, d):
    """하루 기구별 이용 통계(시연용 더미, 날짜+기구 이름 고정 시드). 이용 고객 많은 순으로 반환.
    users: 하루 이용 고객 수 / avg_wait: 하루 평균 대기(분) / peak_wait: 피크 시간대 평균 대기(분)
    peak_users: 피크 시간대(2시간)에 이 기구로 몰린 인원(이용 + 대기)"""
    out = []
    for eq in equipments:
        rng = random.Random(f"peakstat|{eq['name']}|{d.isoformat()}")
        sc = day_congestion(eq, d)
        users = eq_daily_users(eq, d)
        avg_wait = wait_minutes(sc)
        peak_wait = round(avg_wait * 1.9 + rng.uniform(0.5, 1.5), 1) if sc >= 40 else round(rng.uniform(0, 1.5), 1)
        peak_users = int(round(users * rng.uniform(0.20, 0.27)))
        out.append({"eq": eq, "users": users, "avg_wait": avg_wait, "peak_wait": peak_wait, "peak_users": peak_users})
    out.sort(key=lambda r: -r["users"])
    return out


VISITS_PER_MONTH = 12      # 회원 1명의 월평균 방문 횟수 (주 3회 가정)


def eq_daily_users(eq, d):
    """기구 eq의 날짜 d 하루 이용 고객 수 (daily_usage와 같은 더미 공식)"""
    rng = random.Random(f"daily|{eq['name']}|{d.isoformat()}")
    return max(3, int(round(eq["qty"] * day_congestion(eq, d) * 0.42 + rng.uniform(-4, 4))))


def long_wait_week(eq):
    """최근 7일 '대기가 너무 길어요' 건수 = 다른 회원 더미 + 오늘 앱에서 실제로 눌린 건수"""
    base = WEEK_BASE.get(eq["name"], 25 + zlib.crc32(eq["name"].encode("utf-8")) % 50)
    rng = random.Random(f"longwait|{eq['name']}")
    dummy = max(0, int(round((base - 50) * 0.8 + rng.uniform(-3, 3))))
    return dummy + S["long_wait"].get(eq["id"], 0)


def long_share(score):
    """혼잡도별 '5분 이상 기다리는 이용' 비율 (가정 모델: 50%부터 생기기 시작해 100%에서 전원)"""
    return max(0.0, min(1.0, (score - 50) / 50))


def idle_window(slots=2):
    """낮 시간(10~18시) 중 이용 회원이 가장 적은 연속 4시간(2칸)"""
    day = [r for r in TREND if 10 <= int(r[0][:2]) <= 16]
    best = None
    for i in range(len(day) - slots + 1):
        win = day[i:i + slots]
        tot = sum(r[1] for r in win)
        if best is None or tot < best[0]:
            best = (tot, win)
    win = best[1]
    return win, int(win[0][0][:2]), int(win[-1][0][3:5])


def recommend(equipments, days):
    """주간 데이터로 증설/처분(감축) 추천을 만든다. 반환: (증설 목록, 처분 목록, 유지 목록)"""
    expand, dispose, keep = [], [], []
    for eq in equipments:
        st = weekly_stats(eq, days)
        qty, avg = eq["qty"], st["avg"]
        if avg >= EXPAND_AVG:                                   # 판단은 '주 평균 혼잡도' 하나로 단순화
            add = 2 if avg >= 85 else 1
            expand.append({"eq": eq, "stats": st, "add": add, "after": int(round(avg * qty / (qty + add))),
                           "urgent": avg >= 85})
        elif avg <= DISPOSE_AVG:
            if qty >= 2 and avg * qty / (qty - 1) <= DISPOSE_AFTER_MAX:
                dispose.append({"eq": eq, "stats": st, "remove": 1, "after": int(round(avg * qty / (qty - 1))),
                                "urgent": avg <= 25})
            elif qty == 1 and avg <= 20:
                dispose.append({"eq": eq, "stats": st, "remove": 1, "after": None, "urgent": True})
            else:
                keep.append({"eq": eq, "stats": st})
        else:
            keep.append({"eq": eq, "stats": st})
    expand.sort(key=lambda r: -r["stats"]["avg"])
    dispose.sort(key=lambda r: r["stats"]["avg"])
    return expand, dispose, keep


# ======================================================================
# 4. Flet 버전 호환 도우미
# ======================================================================
ICONS = getattr(ft, "Icons", None) or getattr(ft, "icons", None)
BUTTON_CLS = getattr(ft, "Button", None) or ft.ElevatedButton


def ic(name):
    return getattr(ICONS, name, None) if ICONS is not None else None


def mk(cls, *args, **kwargs):
    """버전에 따라 없는 인자(TypeError)는 빼고 생성한다."""
    for _ in range(25):
        try:
            return cls(*args, **kwargs)
        except TypeError as err:
            m = re.search(r"unexpected keyword argument '(\w+)'", str(err))
            if m and m.group(1) in kwargs:
                kwargs.pop(m.group(1))
                continue
            raise
    return cls(*args, **kwargs)


def border_all(width, color):
    try:
        return ft.border.all(width, color)
    except Exception:
        return ft.Border.all(width, color)


def pad(h=0, v=0):
    try:
        return ft.Padding(left=h, top=v, right=h, bottom=v)
    except Exception:
        return ft.padding.symmetric(horizontal=h, vertical=v)


# ======================================================================
# 5. 작은 UI 부품
# ======================================================================
def txt(value, size=14, color=TEXT, bold=False, **kw):
    if size <= 12:          # 11~12px 보조 글자는 1px 키워 가독성 확보
        size += 1
    return ft.Text(value, size=size, color=color, weight=BOLD if bold else None, **kw)


def chip(label, fg, bg):
    return ft.Container(content=txt(label, 11, fg, True), bgcolor=bg, padding=pad(9, 4), border_radius=10)


def part_tag(part):
    # 혼잡 표시(선명한 빨강)와 헷갈리지 않도록 어두운 와인색 배경 + 연한 글자
    return chip(part, WINE_TEXT, WINE)


def make_bar(ratio, color, width=120, height=8):
    """(바깥 트랙, 안쪽 채움) 반환 - 채움 컨트롤의 width/bgcolor를 바꿔 실시간 갱신할 수 있다."""
    ratio = max(0.0, min(1.0, ratio))
    fill = ft.Container(width=max(0.0, width * ratio), height=height, bgcolor=color, border_radius=height / 2)
    track = ft.Container(width=width, height=height, bgcolor=BORDER, border_radius=height / 2,
                         content=ft.Row([fill], spacing=0))
    return track, fill


def bar(ratio, color, width=120, height=8):
    return make_bar(ratio, color, width, height)[0]


def btn(label, on_click=None, icon=None, kind="primary", width=None, height=40):
    # 기본 좌우 여백(약 24px)이 커서 폭이 좁은 버튼에서 한글이 줄바꿈되므로 12px로 줄인다.
    inner = pad(12, 0)
    if kind == "primary":
        style = mk(ft.ButtonStyle, bgcolor=RED, color=WHITE, shape=ft.RoundedRectangleBorder(radius=10),
                   padding=inner)
    elif kind == "outline":
        style = mk(ft.ButtonStyle, bgcolor=CARD, color=WHITE, shape=ft.RoundedRectangleBorder(radius=10),
                   side=ft.BorderSide(1, RED), padding=inner)
    else:
        style = mk(ft.ButtonStyle, bgcolor=CARD_HI, color=TEXT, shape=ft.RoundedRectangleBorder(radius=10),
                   side=ft.BorderSide(1, BORDER), padding=inner)
    return mk(BUTTON_CLS, label, icon=icon, on_click=on_click, style=style, width=width, height=height)


def tf(label, value="", width=None, hint=None, number=False):
    kw = dict(
        label=label, value=value, hint_text=hint, width=width, bgcolor=CARD_HI, border_color=BORDER,
        focused_border_color=RED, color=TEXT, cursor_color=RED, border_radius=10, text_size=14,
        label_style=ft.TextStyle(color=MUTED),
    )
    if number:
        kw["keyboard_type"] = ft.KeyboardType.NUMBER
    return mk(ft.TextField, **kw)


def card(content, **kw):
    return ft.Container(content=content, bgcolor=CARD, border=border_all(1, BORDER),
                        border_radius=16, padding=18, **kw)


def section_title(icon_name, title, sub=None, trailing=None):
    head = [txt(title, 17, TEXT, True)]
    if sub:
        head.append(txt(sub, 12, MUTED))
    left = ft.Row([ft.Icon(ic(icon_name), color=RED, size=22), ft.Column(head, spacing=0)], spacing=10)
    return ft.Row([left] + ([trailing] if trailing is not None else []), alignment=BETWEEN, vertical_alignment=CENTER)


def kpi_card(icon_name, title, value, sub=""):
    return ft.Container(
        col={"xs": 6, "md": 3}, bgcolor=CARD, border=border_all(1, BORDER), border_radius=14, padding=16,
        content=ft.Column([
            ft.Row([ft.Icon(ic(icon_name), color=ICON_MUTED, size=18), txt(title, 12, MUTED)], spacing=6),
            txt(value, 26, TEXT, True),
            txt(sub, 11, MUTED),
        ], spacing=4),
    )


# ======================================================================
# 6. 앱 본체
# ======================================================================
def main(page: ft.Page):
    page.title = "HealF (헬스 프렌드)"
    page.bgcolor = BG
    page.padding = 20
    page.scroll = ft.ScrollMode.AUTO
    page.theme_mode = ft.ThemeMode.DARK
    try:
        page.theme = ft.Theme(color_scheme_seed=RED)
    except Exception:
        pass
    try:
        page.window.width = 1280
        page.window.height = 880
        page.window.min_width = 900
    except Exception:
        try:
            page.window_width = 1280
            page.window_height = 880
        except Exception:
            pass

    # ---------------- 알림(SnackBar / Dialog) ----------------
    def show_overlay(ctrl, kind):
        if hasattr(page, "show_dialog"):          # Flet 1.x
            page.show_dialog(ctrl)
        elif hasattr(page, "open"):               # Flet 0.2x
            page.open(ctrl)
        else:                                     # 아주 구버전
            if kind == "snack":
                page.snack_bar = ctrl
            else:
                page.dialog = ctrl
            ctrl.open = True
            page.update()

    def close_dialog(dlg):
        if hasattr(page, "pop_dialog"):
            page.pop_dialog()
        elif hasattr(page, "close"):
            page.close(dlg)
        else:
            dlg.open = False
            page.update()

    # '사용 가능' 모달 팝업 상태 (동시에 1개만 열린다)
    popup = {"dlg": None, "tid": None, "count": None, "fill": None}
    deferred = []          # 팝업이 열려 있는 동안 밀어둔 SnackBar 메시지
    POP_W = 340            # 팝업 진행바 너비
    TRACK_W = 260          # 예약 현황 카드 진행바 너비

    def toast(msg, kind="info"):
        # 모달 팝업이 열려 있으면 SnackBar를 쌓지 않고 팝업이 닫힌 뒤에 보여준다.
        if popup["dlg"] is not None:
            deferred.append((msg, kind))
            return
        if deferred:
            pend = list(deferred)
            deferred.clear()
            msg = " / ".join(dict.fromkeys([m for m, _ in pend] + [msg]))
            if any(k == "warn" for _, k in pend):
                kind = "warn"
        bg = {"ok": "#14532D", "warn": "#7A1119", "info": "#26262B"}.get(kind, "#26262B")
        icon = {"ok": "CHECK_CIRCLE", "warn": "WARNING_AMBER", "info": "INFO"}.get(kind, "INFO")
        snack = mk(
            ft.SnackBar,
            content=ft.Row([ft.Icon(ic(icon), color=WHITE, size=20), ft.Text(msg, color=WHITE, size=14, expand=True)],
                           spacing=10),
            bgcolor=bg, duration=3600, behavior=ft.SnackBarBehavior.FLOATING,
        )
        show_overlay(snack, "snack")

    def remain_ready(t, now=None):
        return max(0, int(math.ceil(t["expire_at"] - (now or time.time()))))

    def ready_color(r):
        return GREEN if r > 30 else (AMBER if r > 15 else RED)

    def eq_name(t):
        eq = get_eq(t["eq_id"])
        return eq["name"] if eq else "기구"

    def close_popup(tid=None):
        """열려 있는 '사용 가능' 팝업을 닫는다. tid를 주면 그 예약의 팝업일 때만 닫는다."""
        with lock:
            dlg = popup["dlg"]
            if dlg is None or (tid is not None and popup["tid"] != tid):
                return False
            popup.update(dlg=None, tid=None, count=None, fill=None)
        close_dialog(dlg)
        return True

    def maybe_show_popup():
        """'사용 가능'이 됐는데 아직 팝업을 띄우지 않은 예약 1건을 모달로 보여준다."""
        with lock:
            if popup["dlg"] is not None or using_ticket() is not None:
                return          # 다른 팝업이 열려 있거나, 이미 다른 기구를 사용 중이면 띄우지 않는다
            t = next((x for x in S["tickets"] if x["status"] == "ready" and not x["popup_shown"]), None)
            if t is None:
                return
            t["popup_shown"] = True
            tid, name = t["id"], eq_name(t)
            r = remain_ready(t)
            count = txt(f"남은 시간 {r // 60}:{r % 60:02d}", 22, AMBER, True)
            track, fill = make_bar(r / READY_TIMEOUT_SEC, ready_color(r), POP_W, 8)
            dlg = mk(
                ft.AlertDialog, modal=True, bgcolor=CARD,
                title=ft.Row([ft.Icon(ic("NOTIFICATIONS_ACTIVE"), color=RED, size=26),
                              txt("사용 가능 알림", 20, TEXT, True)], spacing=10),
                content=ft.Column([
                    ft.Row([ft.Icon(ic("CHECK_CIRCLE"), color=GREEN, size=22), txt(name, 18, TEXT, True)], spacing=8),
                    txt("앞 사용자가 사용을 마쳤어요. 지금 이용할 수 있어요!", 14, TEXT),
                    count,
                    track,
                    txt(f"{fmt_dur(READY_TIMEOUT_SEC)} 안에 선택하지 않으면 예약이 자동 취소돼요.", 12, MUTED),
                    txt("사용: 바로 이용  ·  미루기: 같은 기구 대기열 맨 뒤로  ·  취소: 예약 취소", 12, MUTED),
                ], tight=True, spacing=10, width=380),
                actions=[
                    btn("취소", on_click=lambda e, i=tid: on_cancel(i), icon=ic("CLOSE"), kind="ghost",
                        width=108, height=42),
                    btn("미루기", on_click=lambda e, i=tid: on_postpone(i), icon=ic("SKIP_NEXT"), kind="outline",
                        width=122, height=42),
                    btn("사용", on_click=lambda e, i=tid: on_use(i), icon=ic("PLAY_ARROW"), width=108, height=42),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            popup.update(dlg=dlg, tid=tid, count=count, fill=fill)
        show_overlay(dlg, "dialog")

    # ======================================================================
    # 관장용 탭
    # ======================================================================
    reg_part = {"v": "가슴"}
    part_row = ft.Row(wrap=True, spacing=8, run_spacing=8)
    f_name = tf("기구 종류", hint="예) 체스트 프레스")
    f_qty = tf("수량 (대)", width=130, hint="예) 3", number=True)
    f_open = tf("오픈", S["gym"]["open"], width=120, hint="06:00")
    f_close = tf("마감", S["gym"]["close"], width=120, hint="23:00")
    hours_text = txt("", 13, TEXT, True)
    kpi_row = ft.ResponsiveRow(spacing=12, run_spacing=12)
    cong_holder = ft.Container()
    cong_sub = txt("", 12, MUTED)
    trend_holder = ft.Container()
    rank_holder = ft.Container()

    def render_part_chips():
        part_row.controls.clear()
        for p in PARTS:
            sel = reg_part["v"] == p
            part_row.controls.append(ft.Container(
                content=txt(p, 13, WHITE if sel else MUTED, True),
                bgcolor=RED if sel else CARD_HI, border=border_all(1, RED if sel else BORDER),
                border_radius=18, padding=pad(14, 7), on_click=lambda e, p=p: select_part(p)))

    def select_part(p):
        reg_part["v"] = p
        render_part_chips()
        page.update()

    def on_register(e):
        name = (f_name.value or "").strip()
        qty_raw = (f_qty.value or "").strip()
        f_name.error_text = None
        f_qty.error_text = None
        ok = True
        if not name:
            f_name.error_text = "기구 종류를 입력해 주세요"
            ok = False
        if not qty_raw.isdigit() or not (1 <= int(qty_raw) <= 99):
            f_qty.error_text = "1~99 사이 숫자"
            ok = False
        if not ok:
            page.update()
            toast("입력값을 확인해 주세요", "warn")
            return
        qty, part = int(qty_raw), reg_part["v"]
        with lock:
            key = name.replace(" ", "").lower()
            exist = next((x for x in S["equipments"] if x["name"].replace(" ", "").lower() == key), None)
            if exist:
                exist["qty"] += qty
                msg = f"'{exist['name']}' 수량을 {qty}대 추가했어요 (총 {exist['qty']}대)"
            else:
                S["equipments"].append({"id": S["next_eid"], "name": name, "part": part, "qty": qty,
                                        "others_occ": 0, "others_wait": 0, "use_log": []})
                S["next_eid"] += 1
                msg = f"'{name}' ({part}) {qty}대 등록 완료! 회원 화면에 바로 반영돼요"
        f_name.value = ""
        f_qty.value = ""
        render_all()
        toast(msg, "ok")

    def to_min(s):
        h, m = s.split(":")
        return int(h) * 60 + int(m)

    def on_save_hours(e):
        o, c = (f_open.value or "").strip(), (f_close.value or "").strip()
        f_open.error_text = None
        f_close.error_text = None
        bad = False
        if not TIME_RE.match(o):
            f_open.error_text = "HH:MM"
            bad = True
        if not (TIME_RE.match(c) or c == "24:00"):
            f_close.error_text = "HH:MM"
            bad = True
        if not bad and to_min(o) >= to_min(c):
            f_close.error_text = "오픈보다 늦게"
            bad = True
        if bad:
            page.update()
            toast("영업시간 형식을 확인해 주세요 (예: 06:00 ~ 23:00)", "warn")
            return
        S["gym"] = {"open": o, "close": c}
        hours_text.value = f"영업시간  {o} ~ {c}"
        page.update()
        toast(f"영업시간을 {o} ~ {c} 로 저장했어요", "ok")

    def on_reset(e):
        with lock:
            tab = S["tab"]
            reset_state()
            S["tab"] = tab          # 초기화해도 보고 있던 탭은 유지
        f_open.value, f_close.value = S["gym"]["open"], S["gym"]["close"]
        hours_text.value = f"영업시간  {S['gym']['open']} ~ {S['gym']['close']}"
        reg_part["v"] = "가슴"
        render_part_chips()
        render_all()
        toast("시연 데이터를 처음 상태로 되돌렸어요", "info")

    def on_simulate(e):
        simulate_refresh()
        render_all()
        toast("실시간 혼잡도를 갱신했어요 (시뮬레이션)", "info")

    def build_congestion_table():
        rows = []
        for eq in sorted(S["equipments"], key=lambda x: -congestion(x)):
            sc = congestion(eq)
            label, color, soft = level(sc)
            rows.append(ft.DataRow(cells=[
                ft.DataCell(txt(eq["name"], 14, TEXT, True)),
                ft.DataCell(part_tag(eq["part"])),
                ft.DataCell(txt(f"{occupied(eq)} / {eq['qty']}대", 14, TEXT)),
                ft.DataCell(txt(f"{waiting_total(eq)}명", 14, TEXT if waiting_total(eq) else MUTED, waiting_total(eq) > 0)),
                ft.DataCell(ft.Column([
                    txt(wait_label(eq), 14, wait_color(est_wait_min(eq)), True),
                    txt(f"1회 평균 {avg_use_min(eq):.1f}분", 11, MUTED),
                ], spacing=0, tight=True)),
                ft.DataCell(ft.Row([bar(sc / 100, color, 90), txt(f"{sc}% {label}", 13, color, True)], spacing=8)),
            ]))
        heads = ["기구", "부위", "이용 중", "대기", "예상 대기", "현재 혼잡도"]
        table = mk(ft.DataTable, columns=[ft.DataColumn(txt(h, 12, MUTED, True)) for h in heads], rows=rows,
                   heading_row_color=CARD_HI, divider_thickness=0.5, column_spacing=26)
        return ft.Column([ft.Row([table], scroll=ft.ScrollMode.AUTO)], scroll=ft.ScrollMode.AUTO, height=460)

    def wcol_min(m):
        return RED if m >= 8 else (AMBER if m >= 4 else GREEN)

    def build_peak_view(stats):
        """오늘의 피크 시간대 요약 + 그때 기구별 대기·몰린 인원"""
        slot, users, wait, _, _ = max(TREND, key=lambda r: r[1])
        total_users = sum(r[1] for r in TREND)
        healf_total = sum(r[4] for r in TREND)
        summary = ft.Row([
            ft.Column([txt("피크 시간대", 12, MUTED), txt(slot, 26, TEXT, True)], spacing=0),
            ft.Column([txt("이용 회원", 12, MUTED), txt(f"{users}명", 20, TEXT, True),
                       txt(f"하루 {total_users}명 중 {int(round(100 * users / total_users))}%", 11, MUTED)], spacing=0),
            ft.Column([txt("평균 대기", 12, MUTED), txt(f"{wait}분", 20, wcol_min(wait), True)], spacing=0),
        ], spacing=28, wrap=True)
        top = sorted(stats, key=lambda r: -r["peak_wait"])[:6]
        mx = max((r["peak_users"] for r in top), default=1) or 1
        rows = []
        for r in top:
            eq = r["eq"]
            rows.append(ft.DataRow(cells=[
                ft.DataCell(txt(eq["name"], 14, TEXT, True)),
                ft.DataCell(ft.Row([bar(r["peak_users"] / mx, BAR_NEUTRAL, 70), txt(f"{r['peak_users']}명", 13, TEXT)],
                                   spacing=6)),
                ft.DataCell(txt(f"{r['peak_wait']}분", 14, wcol_min(r["peak_wait"]), True)),
                ft.DataCell(txt(f"{r['avg_wait']}분", 13, MUTED)),
            ]))
        heads = ["기구", "몰린 인원", "피크 대기", "하루 평균"]
        table = mk(ft.DataTable, columns=[ft.DataColumn(txt(h, 12, MUTED, True)) for h in heads], rows=rows,
                   heading_row_color=CARD_HI, divider_thickness=0.5, column_spacing=20)
        return ft.Column([
            summary,
            txt(f"피크 때 대기가 길었던 기구 TOP {len(top)}", 13, TEXT, True),
            ft.Row([table], scroll=ft.ScrollMode.AUTO),
            txt(f"오늘 HealF가 한산한 기구를 추천해 분산된 이용 {healf_total}건 · "
                "몰린 인원 = 피크 2시간 동안 그 기구를 이용하거나 기다린 고객 수", 11, MUTED),
        ], spacing=12)

    def build_rank_view(stats):
        """오늘 하루 기구 이용 순위 차트 (이용 고객 많은 순)"""
        mx = max((r["users"] for r in stats), default=1) or 1
        rows = []
        for i, r in enumerate(stats, 1):
            eq = r["eq"]
            medal = RED if i <= 3 else CARD_HI
            rows.append(ft.Row([
                ft.Container(txt(str(i), 12, WHITE if i <= 3 else MUTED, True), bgcolor=medal, width=26, height=26,
                             border_radius=13, alignment=ft.Alignment(0, 0)),
                ft.Container(ft.Row([txt(eq["name"], 13, TEXT, True), part_tag(eq["part"])], spacing=6), width=230),
                bar(r["users"] / mx, BAR_NEUTRAL if i > 3 else MUTED, 190, 10),
                ft.Container(txt(f"{r['users']}명", 13, TEXT, True), width=52),
                txt(f"평균 대기 {r['avg_wait']}분", 13, wcol_min(r["avg_wait"]), True),
            ], spacing=10, vertical_alignment=CENTER))
        total = sum(r["users"] for r in stats)
        return ft.Column([
            txt(f"전체 기구 이용 {total}건 · 이용 고객 많은 순 (한 고객이 여러 기구를 쓰면 기구마다 집계)", 12, MUTED),
            ft.Column(rows, spacing=8, scroll=ft.ScrollMode.AUTO, height=430),
        ], spacing=10)

    def render_admin():
        eqs = S["equipments"]
        total_qty = sum(e["qty"] for e in eqs)
        occ = sum(occupied(e) for e in eqs)
        scores = [congestion(e) for e in eqs]
        avg = round(sum(scores) / len(scores)) if scores else 0
        waiting = sum(waiting_total(e) for e in eqs)
        kpi_row.controls = [
            kpi_card("FITNESS_CENTER", "현재 이용 중 기구", f"{occ} / {total_qty}대", f"등록 {len(eqs)}종"),
            kpi_card("SPEED", "평균 혼잡도", f"{avg}%", level(avg)[0] + " 수준"),
            kpi_card("GROUPS", "총 대기 인원", f"{waiting}명", f"현재 이용 고객 {present_customers()}명 중"),
            kpi_card("EVENT_AVAILABLE", "오늘 HealF 예약", f"{S['reservations']}건",
                     f"예약 후 운동 완료 {S['completed']}건 "
                     f"({int(round(100 * S['completed'] / max(S['reservations'], 1)))}%)"),
        ]
        cong_sub.value = (f"등록 기구 {len(eqs)}종 · 총 {total_qty}대 · 혼잡도 높은 순 · "
                          f"예상 대기 = 대기 인원 ÷ 대수 × 기구별 1회 평균 이용시간 (사용 기록으로 자동 갱신)")
        cong_holder.content = build_congestion_table()
        stats = daily_usage(eqs, datetime.now().date())
        trend_holder.content = build_peak_view(stats)
        rank_holder.content = build_rank_view(stats)

    # ======================================================================
    # 회원용 탭
    # ======================================================================
    top_strip = ft.Row(wrap=True, spacing=8, run_spacing=8)
    mpart_row = ft.Row(wrap=True, spacing=8, run_spacing=8)
    live_banner = ft.Container(bgcolor=CARD, border_radius=16, padding=18)
    grid = ft.ResponsiveRow(spacing=12, run_spacing=12)
    ticket_col = ft.Column(spacing=10)
    ticket_badge = txt("0/3", 12, TEXT, True)
    routine_col = ft.Column(spacing=10)
    part_checks = {}
    R = {"ids": []}
    live = {}              # 예약 id -> 매초 갱신할 컨트롤 {"info": Text, "fill": Container | None}

    def on_reserve(eid):
        r = do_reserve(eid)
        if not r:
            return
        if r.get("error") == "limit":
            toast(f"예약은 최대 {MAX_RESERVATIONS}개까지 가능해요. "
                  f"사용 완료하거나 취소한 뒤 다시 예약해 주세요", "warn")
            return
        render_all()
        n = len(S["tickets"])
        if r["status"] == "timing":
            est = f"예상 대기 약 {r['est']}분" if r["est"] else "바로 사용 가능"
            toast(f"'{r['name']}' 예약 완료! 내 앞 대기 {r['ahead']}명 · {est} · 곧 알림을 드릴게요 (예약 {n}/{MAX_RESERVATIONS})",
                  "ok")
        else:
            toast(f"'{r['name']}' 중복 예약 접수 (내 예약 {r['mine']}건) · 앞선 예약이 끝나면 순서가 시작돼요 "
                  f"(예약 {n}/{MAX_RESERVATIONS})", "ok")

    def stale_toast():
        toast("이미 처리됐거나 시간이 지나 취소된 예약이에요", "warn")

    def on_use(tid):
        """[사용] 사용 가능 상태의 기구를 지금 사용한다."""
        close_popup(tid)
        with lock:
            t = find_ticket(tid)
            ok = t is not None and t["status"] == "ready"
            cur = using_ticket() if ok else None
            if ok and cur is None:
                use_ticket(t)
                name = eq_name(t)
                others = [eq_name(x) for x in S["tickets"] if x["status"] == "ready"]
            elif ok:
                cur_name = eq_name(cur)
        if ok and cur is not None:
            # 한 번에 한 기구만 사용할 수 있다
            toast(f"이미 '{cur_name}'을(를) 사용 중이에요. 한 번에 한 기구만 사용할 수 있어요 · "
                  f"[사용 완료] 후 이용해 주세요", "warn")
            return
        render_all()
        if ok:
            msg = f"'{name}' 사용을 시작했어요! 운동이 끝나면 [사용 완료]를 눌러주세요 (최대 {fmt_dur(MAX_USE_SEC)})"
            if others:
                msg += f" · '{', '.join(others)}'은(는) 사용 완료 후 이용할 수 있어요"
            toast(msg, "ok")
        else:
            stale_toast()
        maybe_show_popup()

    def on_cancel(tid):
        """[취소] 예약을 취소한다 (대기 중 / 사용 가능 상태 모두)."""
        close_popup(tid)
        with lock:
            t = find_ticket(tid)
            ok = t is not None and t["status"] != "using"
            if ok:
                name, was_ready = eq_name(t), t["status"] == "ready"
                handed = cancel_ticket(t)
        render_all()
        if ok:
            extra = " · 대기 중이던 다음 회원에게 바로 인계됐어요" if (was_ready and handed) else ""
            toast(f"'{name}' 예약을 취소했어요{extra}", "info")
        else:
            stale_toast()
        maybe_show_popup()

    def on_postpone(tid):
        """[미루기] 같은 기구의 예약 순위 맨 뒤로 보내고 다시 대기한다."""
        close_popup(tid)
        with lock:
            t = find_ticket(tid)
            ok = t is not None and t["status"] == "ready"
            if ok:
                postpone_ticket(t)
                name, st, ahead = eq_name(t), t["status"], t["ahead"]
        render_all()
        if ok:
            if st == "timing":
                toast(f"'{name}' 예약을 대기열 맨 뒤로 미뤘어요 · 내 앞 대기 {ahead}명, 순서가 되면 다시 알려드릴게요", "info")
            else:
                toast(f"'{name}' 예약을 맨 뒤로 미뤘어요 · 같은 기구의 앞선 내 예약부터 먼저 진행돼요", "info")
        else:
            stale_toast()
        maybe_show_popup()

    def on_complete(tid):
        with lock:
            t = find_ticket(tid)
            if not t or t["status"] != "using":
                return
            name = eq_name(t)
            handed = finish_ticket(t)       # 대기 중이던 다음 회원에게 인계
        render_all()
        if handed:
            toast(f"'{name}' 사용 완료! 대기 중이던 다음 회원에게 바로 인계됐어요 (회전율 UP) · 이용시간이 예상 대기에 반영돼요", "ok")
        else:
            toast(f"'{name}' 사용 완료! 이제 다른 회원이 이용할 수 있어요 · 이용시간이 예상 대기에 반영돼요", "ok")
        maybe_show_popup()          # 사용 중이라 못 띄운 '사용 가능' 알림이 있으면 이어서 띄운다

    def equip_card(eq):
        sc = congestion(eq)
        label, color, soft = level(sc)
        free, wait = free_units(eq), waiting_total(eq)
        mine = [t for t in S["tickets"] if t["eq_id"] == eq["id"]]
        using = any(t["status"] == "using" for t in mine)
        ready = any(t["status"] == "ready" for t in mine)
        full = len(S["tickets"]) >= MAX_RESERVATIONS
        chips = [chip(label, color, soft)]
        if using:
            chips.insert(0, chip("내가 사용 중", WHITE, RED))
        elif ready:
            chips.insert(0, chip("내 차례 · 선택 필요", GREEN, GREEN_SOFT))
        elif mine:
            chips.insert(0, chip(f"내 예약 {len(mine)}건", RED, RED_SOFT))
        return ft.Container(
            col={"xs": 12, "md": 6}, bgcolor=CARD_HI, border_radius=14, padding=14,
            border=border_all(1, RED if using else (GREEN if ready else BORDER)),
            content=ft.Column([
                ft.Row([
                    ft.Container(ft.Icon(ic("FITNESS_CENTER"), color=ICON_MUTED, size=20), bgcolor=CARD,
                                 padding=8, border_radius=10),
                    ft.Column([txt(eq["name"], 15, TEXT, True), part_tag(eq["part"])], spacing=3),
                ], spacing=10),
                ft.Row(chips, spacing=6, wrap=True),
                ft.Row([bar(sc / 100, color, 150), txt(f"{sc}%", 13, color, True)], spacing=8),
                ft.Row([
                    ft.Column([
                        txt("예상 대기 " + wait_label(eq), 15, wait_color(est_wait_min(eq)), True),
                        txt(f"이용 가능 {free}/{eq['qty']}대 · 대기 {wait}명", 12, MUTED),
                    ], spacing=1),
                    btn("예약", on_click=lambda e, i=eq["id"]: on_reserve(i), icon=ic("ADD"), height=38, width=96,
                        kind="ghost" if full else "primary"),
                ], alignment=BETWEEN, vertical_alignment=CENTER),
            ] + long_wait_row(eq, wait), spacing=9),
        )

    def long_wait_row(eq, wait):
        """대기가 있는 기구에만 '대기가 너무 길어요' 버튼. 회원 1명은 하루 한 번만 보낼 수 있다."""
        today = datetime.now().date().isoformat()
        mine = S["my_long_wait"]
        if mine and mine[0] == today and mine[1] == eq["id"]:
            return [ft.Row([ft.Icon(ic("CHECK_CIRCLE"), color=MUTED, size=16),
                            txt("'대기가 너무 길어요' 의견을 보냈어요 · 관장님께 전달돼요", 12, MUTED)], spacing=6)]
        if wait == 0:
            return []
        used = bool(mine and mine[0] == today)
        return [ft.Container(
            content=ft.Row([ft.Icon(ic("SENTIMENT_DISSATISFIED"), color=MUTED if used else AMBER, size=16),
                            txt("대기가 너무 길어요", 12, MUTED if used else AMBER, True)], spacing=6, tight=True),
            bgcolor=CARD, border=border_all(1, BORDER if used else AMBER_SOFT), border_radius=14, padding=pad(10, 5),
            on_click=lambda e, i=eq["id"]: on_long_wait(i))]

    def on_long_wait(eid):
        today = datetime.now().date().isoformat()
        with lock:
            mine = S["my_long_wait"]
            if mine and mine[0] == today:
                prev = get_eq(mine[1])
                done = False
            else:
                S["my_long_wait"] = (today, eid)
                S["long_wait"][eid] = S["long_wait"].get(eid, 0) + 1
                done = True
            name = (get_eq(eid) or {}).get("name", "기구")
        if not done:
            toast(f"'대기가 너무 길어요'는 하루 한 번만 보낼 수 있어요 (오늘: '{prev['name'] if prev else '기구'}')", "warn")
            return
        render_all()
        toast(f"'{name}' 대기가 길다는 의견을 보냈어요 · 증설 검토 자료로 관장님께 전달돼요", "ok")

    def info_text(t, now):
        st = t["status"]
        if st == "timing":
            remain = max(0, int(math.ceil(t["ready_at"] - now)))
            ahead_now = int(math.ceil(t["ahead"] * remain / t["delay"])) if t["delay"] else 0
            return f"내 앞 대기 {ahead_now}명 · 약 {remain}초 후 알림"
        if st == "waiting":
            if t.get("postponed"):
                return "대기열 맨 뒤로 이동했어요 · 같은 기구의 앞선 내 예약이 끝나면 시작돼요"
            return "앞선 내 예약이 끝나면 순서가 시작돼요"
        if st == "ready":
            r = remain_ready(t, now)
            cur = using_ticket()
            if cur is not None:
                return f"남은 시간 {r // 60}:{r % 60:02d} · '{eq_name(cur)}' 사용 완료 후 이용할 수 있어요"
            return f"남은 시간 {r // 60}:{r % 60:02d} · 선택하지 않으면 자동 취소"
        el = int(now - t["started_at"])
        left = max(0, int(math.ceil(t["end_at"] - now)))
        return f"이용 중 · {el // 60:02d}:{el % 60:02d} 경과 · 남은 이용시간 {left // 60:02d}:{left % 60:02d}"

    def ticket_row(t):
        name, tid, st = eq_name(t), t["id"], t["status"]
        now = time.time()
        lv = {"info": txt(info_text(t, now), 12, AMBER if st == "ready" else MUTED, st == "ready"), "fill": None}
        live[tid] = lv
        extra = [chip(f"미룸 {t['postponed']}회", MUTED, BORDER)] if t.get("postponed") else []
        below = []
        action = None
        if st == "timing":
            tag = chip("대기 중", AMBER, AMBER_SOFT)
            action = btn("취소", on_click=lambda e, i=tid: on_cancel(i), kind="ghost", height=38, width=84)
        elif st == "waiting":
            tag = chip("순서 대기", MUTED, BORDER)
            action = btn("취소", on_click=lambda e, i=tid: on_cancel(i), kind="ghost", height=38, width=84)
        elif st == "ready":
            tag = chip("사용 가능", GREEN, GREEN_SOFT)
            r = remain_ready(t, now)
            track, fill = make_bar(r / READY_TIMEOUT_SEC, ready_color(r), TRACK_W, 8)
            lv["fill"] = fill
            blocked = using_ticket() is not None        # 다른 기구를 사용 중이면 [사용] 불가 (누르면 안내)
            below = [track, ft.Row([
                btn("사용", on_click=lambda e, i=tid: on_use(i), icon=ic("PLAY_ARROW"), height=40, width=104,
                    kind="ghost" if blocked else "primary"),
                btn("미루기", on_click=lambda e, i=tid: on_postpone(i), icon=ic("SKIP_NEXT"), kind="outline",
                    height=40, width=118),
                btn("취소", on_click=lambda e, i=tid: on_cancel(i), icon=ic("CLOSE"), kind="ghost",
                    height=40, width=104),
            ], spacing=8, wrap=True)]
        else:
            tag = chip("사용 중", WHITE, RED)
            track, fill = make_bar(max(0, t["end_at"] - now) / MAX_USE_SEC, RED, TRACK_W, 8)
            lv["fill"] = fill
            below = [track]
            action = btn("사용 완료", on_click=lambda e, i=tid: on_complete(i), icon=ic("DONE_ALL"),
                         height=40, width=136)
        border_color = {"using": RED, "ready": GREEN}.get(st, BORDER)
        left = ft.Column([ft.Row([txt(name, 14, TEXT, True), tag] + extra, spacing=8, wrap=True), lv["info"]] + below,
                         spacing=6, expand=True)
        return ft.Container(
            bgcolor=CARD_HI, border_radius=12, padding=12, border=border_all(1, border_color),
            content=ft.Row([left] + ([action] if action is not None else []), vertical_alignment=CENTER),
        )

    def refresh_live():
        """상태 변화가 없는 동안에는 컨트롤을 새로 만들지 않고 글자/진행바만 갱신한다 (버튼 클릭 유실 방지)."""
        with lock:
            now = time.time()
            for t in S["tickets"]:
                lv = live.get(t["id"])
                if not lv:
                    continue
                lv["info"].value = info_text(t, now)
                if lv["fill"] is None:
                    continue
                if t["status"] == "ready":
                    r = remain_ready(t, now)
                    lv["fill"].width = TRACK_W * r / READY_TIMEOUT_SEC
                    lv["fill"].bgcolor = ready_color(r)
                elif t["status"] == "using":
                    lv["fill"].width = TRACK_W * max(0.0, t["end_at"] - now) / MAX_USE_SEC
            if popup["dlg"] is not None:
                t = find_ticket(popup["tid"])
                if t is not None and t["status"] == "ready":
                    r = remain_ready(t, now)
                    popup["count"].value = f"남은 시간 {r // 60}:{r % 60:02d}"
                    popup["fill"].width = POP_W * r / READY_TIMEOUT_SEC
                    popup["fill"].bgcolor = ready_color(r)

    def render_tickets():
        tks = S["tickets"]
        ticket_col.controls.clear()
        live.clear()
        if not tks:
            ticket_col.controls.append(ft.Container(
                content=txt("예약 내역이 없어요. 왼쪽 기구 카드에서 [예약]을 눌러보세요.", 13, MUTED),
                padding=pad(4, 10)))
        else:
            for t in tks:
                ticket_col.controls.append(ticket_row(t))
        full = len(tks) >= MAX_RESERVATIONS
        ticket_badge.value = f"{len(tks)}/{MAX_RESERVATIONS}" + (" · 가득 참" if full else "")
        ticket_badge.color = RED if full else TEXT

    def select_mpart(p):
        S["mpart"] = p
        render_member()
        page.update()

    def render_mpart_tabs():
        mpart_row.controls.clear()
        for p in ["전체"] + PARTS:
            n = len(S["equipments"]) if p == "전체" else sum(1 for e in S["equipments"] if e["part"] == p)
            if p != "전체" and n == 0:
                continue
            sel = S["mpart"] == p
            mpart_row.controls.append(ft.Container(
                content=txt(f"{p} {n}", 13, WHITE if sel else MUTED, True),
                bgcolor=RED if sel else CARD_HI, border=border_all(1, RED if sel else BORDER),
                border_radius=18, padding=pad(14, 7), on_click=lambda e, p=p: select_mpart(p)))

    def render_live_banner():
        eqs = S["equipments"]
        scores = [congestion(e) for e in eqs]
        avg = round(sum(scores) / len(scores)) if scores else 0
        state, color, soft = level(avg)
        word = {"여유": "한산해요", "보통": "보통이에요", "혼잡": "붐벼요"}[state]
        waits = [est_wait_min(e) for e in eqs]
        long_cnt = sum(1 for m in waits if m >= 5)
        _, h0, h1 = idle_window()
        plan = {"window": f"{h0:02d}~{h1:02d}시"}
        live_banner.content = ft.ResponsiveRow([
            ft.Container(col={"xs": 12, "md": 4}, content=ft.Column([
                ft.Row([ft.Icon(ic("SENSORS"), color=color, size=16), txt("지금 우리 헬스장", 12, MUTED, True)], spacing=6),
                txt(f"지금은 {word}", 22, color, True),
                txt(f"{datetime.now().strftime('%H:%M')} 기준 · 회원 전용 · 헬스장 밖에서도 확인", 11, MUTED),
            ], spacing=2)),
            ft.Container(col={"xs": 4, "md": 2}, content=ft.Column([
                txt("이용 고객", 12, MUTED), txt(f"{present_customers()}명", 18, TEXT, True)], spacing=0)),
            ft.Container(col={"xs": 4, "md": 2}, content=ft.Column([
                txt("평균 혼잡도", 12, MUTED), txt(f"{avg}%", 18, color, True)], spacing=0)),
            ft.Container(col={"xs": 4, "md": 2}, content=ft.Column([
                txt("5분↑ 대기 기구", 12, MUTED), txt(f"{long_cnt}종", 18, AMBER if long_cnt else GREEN, True)], spacing=0)),
            ft.Container(col={"xs": 12, "md": 2}, content=ft.Column([
                txt("한산한 시간", 12, MUTED), txt(plan["window"], 18, GREEN, True)], spacing=0)),
        ], spacing=12, run_spacing=8, vertical_alignment=CENTER)
        live_banner.border = border_all(1, soft)

    def render_member():
        render_live_banner()
        if S["mpart"] != "전체" and not any(e["part"] == S["mpart"] for e in S["equipments"]):
            S["mpart"] = "전체"
        render_mpart_tabs()
        eqs = [e for e in S["equipments"] if S["mpart"] == "전체" or e["part"] == S["mpart"]]
        low = sorted(eqs, key=lambda x: (congestion(x), -free_units(x)))[:3]
        where = "" if S["mpart"] == "전체" else f" ({S['mpart']})"
        top_strip.controls = [ft.Icon(ic("BOLT"), color=AMBER, size=18), txt(f"지금 가장 한산한 기구{where}", 13, TEXT, True)]
        for x in low:
            sc = congestion(x)
            _, color, soft = level(sc)
            top_strip.controls.append(chip(f"{x['name']} · {sc}%", color, soft))
        # 부위 탭 안에서는 가나다순, '전체'에서는 부위 순서대로 묶은 뒤 부위 안에서 가나다순
        order = {p: i for i, p in enumerate(PARTS)}
        eqs = sorted(eqs, key=lambda x: (order.get(x["part"], 99), x["name"]))
        grid.controls = [equip_card(x) for x in eqs]
        render_tickets()

    # ---------------- 케어 요청 ----------------
    care_detail = tf("자세한 내용 (선택)", hint="예) 레그 프레스 2번 시트에 땀이 남아 있어요", width=520)
    care_cat_row = ft.Row(wrap=True, spacing=8, run_spacing=8)
    care_mine_col = ft.Column(spacing=8)
    care_inbox_col = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, height=360)
    care_kpi = txt("", 12, MUTED)
    STATUS_STYLE = {"접수": (AMBER, AMBER_SOFT), "확인": (TEXT, BORDER), "완료": (GREEN, GREEN_SOFT)}
    DEFAULT_REPLY = {"확인": "확인했습니다. 검토 후 반영할게요.", "완료": "확인 후 조치했어요. 알려주셔서 감사합니다!"}
    REPLY_TEMPLATES = ["확인했습니다. 검토 후 반영할게요.",
                       "말씀하신 부분 바로 조치할게요. 감사합니다!",
                       "회원님마다 의견이 달라서, 더 많은 분들의 의견에 맞춰 조정할게요.",
                       "불편을 드려 죄송합니다. 직원 교육과 안내를 강화할게요."]

    def ago(t):
        m = int((datetime.now() - t).total_seconds() // 60)
        return "방금" if m < 1 else (f"{m}분 전" if m < 60 else f"{m // 60}시간 전")

    def select_care(c):
        S["care_sel"] = c
        render_care()
        page.update()

    def on_send_care(e):
        cat = S["care_sel"]
        detail = (care_detail.value or "").strip()[:80]
        today = datetime.now().date()
        with lock:
            dup = any(x["mine"] and x["cat"] == cat and x["at"].date() == today for x in S["care"])
            if not dup:
                S["care"].insert(0, {"id": S["next_cid"], "cat": cat, "detail": detail, "at": datetime.now(),
                                     "status": "접수", "mine": True, "reply": ""})
                S["next_cid"] += 1
        if dup:
            toast(f"'{cat}'은(는) 오늘 이미 보냈어요 · 같은 요청은 하루 한 번만 보낼 수 있어요", "warn")
            return
        care_detail.value = ""
        render_all()
        toast(f"'{cat}' 요청을 익명으로 관장님께 보냈어요 · 처리되면 여기서 알려드려요", "ok")

    def set_care(cid, status=None, reply=None):
        """요청 상태/답변 변경. status: '확인' 또는 '완료' (None이면 유지), reply: 관장 답변 (None이면 기본 답변)"""
        with lock:
            x = next((c for c in S["care"] if c["id"] == cid), None)
            if not x:
                return None
            if status and not (x["status"] == "완료" and status == "확인"):
                x["status"] = status
            elif x["status"] == "접수":
                x["status"] = "확인"          # 답변만 보내도 '확인' 처리
            if reply:
                x["reply"] = reply
            elif not x["reply"] or x["reply"] in DEFAULT_REPLY.values():
                x["reply"] = DEFAULT_REPLY.get(x["status"], x["reply"])
            return x["cat"], x["status"]

    def on_care_status(cid, status):
        r = set_care(cid, status)
        if not r:
            return
        render_all()
        cat, st = r
        toast(f"'{cat}' 요청에 '{'확인했어요' if st == '확인' else '처리 완료'}' 답변을 보냈어요", "ok")

    def open_reply(cid):
        x = next((c for c in S["care"] if c["id"] == cid), None)
        if not x:
            return
        field = mk(ft.TextField, label="관장님 답변", value=x["reply"], multiline=True, min_lines=2, max_lines=4,
                   max_length=120, bgcolor=CARD_HI, border_color=BORDER, focused_border_color=RED, color=TEXT,
                   cursor_color=RED, border_radius=10, text_size=14, label_style=ft.TextStyle(color=MUTED), width=420)

        def use_tpl(t):
            field.value = t
            page.update()

        tpl = [ft.Container(content=txt(t, 12, MUTED), bgcolor=CARD_HI, border=border_all(1, BORDER), border_radius=12,
                            padding=pad(10, 6), on_click=lambda e, t=t: use_tpl(t)) for t in REPLY_TEMPLATES]
        dlg = None

        def send(status):
            text = (field.value or "").strip()
            if not text:
                field.error_text = "답변을 입력하거나 아래 문장을 골라 주세요"
                page.update()
                return
            close_dialog(dlg)
            r = set_care(cid, status, text)
            render_all()
            if r:
                toast(f"'{r[0]}' 요청에 답변을 보냈어요 · 상태: {r[1]}", "ok")

        dlg = mk(
            ft.AlertDialog, modal=True, bgcolor=CARD,
            title=ft.Row([ft.Icon(ic(CARE_ICON.get(x["cat"], "CHAT_BUBBLE_OUTLINE")), color=MUTED, size=22),
                          txt(x["cat"], 18, TEXT, True)], spacing=8),
            content=ft.Column([
                txt(x["detail"] or "(내용 없음)", 13, MUTED),
                field,
                txt("자주 쓰는 답변", 12, MUTED, True),
                ft.Column(tpl, spacing=6),
            ], tight=True, spacing=10, width=440),
            actions=[
                btn("닫기", on_click=lambda e: close_dialog(dlg), kind="ghost", width=90, height=40),
                btn("답변 보내기", on_click=lambda e: send(None), kind="outline", width=130, height=40),
                btn("답변 + 처리 완료", on_click=lambda e: send("완료"), width=160, height=40),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        show_overlay(dlg, "dialog")

    def care_row(x, admin):
        fg, bg = STATUS_STYLE[x["status"]]
        head = [ft.Icon(ic(CARE_ICON.get(x["cat"], "CHAT_BUBBLE_OUTLINE")), color=MUTED, size=18),
                txt(x["cat"], 14, TEXT, True), chip(x["status"], fg, bg), txt(ago(x["at"]), 12, MUTED)]
        if admin and x["mine"]:
            head.append(chip("시연 회원", MUTED, BORDER))
        body = [ft.Row(head, spacing=8, wrap=True, vertical_alignment=CENTER)]
        if x["detail"]:
            body.append(txt(x["detail"], 13, TEXT))
        if x["reply"]:
            body.append(txt(f"관장님 답변 · {x['reply']}", 12, GREEN, True))
        action = None
        if admin:
            bs = []
            if x["status"] == "접수":
                bs.append(btn("확인했어요", on_click=lambda e, i=x["id"]: on_care_status(i, "확인"), kind="outline",
                              height=34, width=112))
            bs.append(btn("답변 쓰기", on_click=lambda e, i=x["id"]: open_reply(i), kind="ghost", height=34, width=104))
            if x["status"] != "완료":
                bs.append(btn("처리 완료", on_click=lambda e, i=x["id"]: on_care_status(i, "완료"), height=34, width=104))
            action = ft.Column(bs, spacing=6, horizontal_alignment=ft.CrossAxisAlignment.END)
        return ft.Container(
            bgcolor=CARD_HI, border_radius=12, padding=12, border=border_all(1, BORDER),
            content=ft.Row([ft.Column(body, spacing=4, expand=True)] + ([action] if action else []),
                           vertical_alignment=CENTER))

    def render_care():
        care_cat_row.controls.clear()
        for c, icon in CARE_CATS:
            sel = S["care_sel"] == c
            care_cat_row.controls.append(ft.Container(
                content=ft.Row([ft.Icon(ic(icon), color=WHITE if sel else MUTED, size=16),
                                txt(c, 13, WHITE if sel else MUTED, True)], spacing=6, tight=True),
                bgcolor=RED if sel else CARD_HI, border=border_all(1, RED if sel else BORDER), border_radius=18,
                padding=pad(14, 8), on_click=lambda e, c=c: select_care(c)))
        mine = [x for x in S["care"] if x["mine"]]
        care_mine_col.controls = [care_row(x, False) for x in mine] or [
            txt("보낸 요청이 없어요. 불편한 점이 있으면 편하게 알려주세요.", 13, MUTED)]
        # 관장 요청함: 처리 안 된 것 먼저, 최신 순
        order = {"접수": 0, "확인": 1, "완료": 2}
        today = datetime.now().date()
        todays = [x for x in S["care"] if x["at"].date() == today]
        items = sorted(todays, key=lambda x: (order[x["status"]], -x["at"].timestamp()))
        care_inbox_col.controls = [care_row(x, True) for x in items] or [txt("오늘 들어온 요청이 없어요", 13, MUTED)]
        new_n = sum(1 for x in todays if x["status"] == "접수")
        open_n = sum(1 for x in todays if x["status"] != "완료")
        care_kpi.value = f"새 요청 {new_n}건 · 미완료 {open_n}건 · 오늘 전체 {len(todays)}건 · 회원은 익명으로 보내요"
        if care_rep["open"]:
            render_care_report()

    # ---------------- 이번 주 회원 케어 분석 ----------------
    care_rep = {"open": False}
    care_report_body = ft.Container()

    def render_care_report():
        days = week_days(datetime.now().date())
        counts = care_week_counts(days, S["care"])
        totals = {c: sum(v) for c, v in counts.items()}
        total = sum(totals.values())
        daily = [sum(counts[c][i] for c in counts) for i in range(7)]
        today_items = [x for x in S["care"] if x["at"].date() == days[-1]]
        past = total - len(today_items)
        done = int(round(past * 0.78)) + sum(1 for x in today_items if x["status"] == "완료")
        acked = int(round(past * 0.15)) + sum(1 for x in today_items if x["status"] == "확인")
        rate = int(round(100 * (done + acked) / total)) if total else 0
        ranked = sorted(totals.items(), key=lambda kv: -kv[1])
        top_cat = ranked[0][0] if ranked and ranked[0][1] else "-"
        kpis = ft.ResponsiveRow([
            kpi_card("INBOX", "이번 주 요청", f"{total}건", f"{day_label(days[0])} ~ {day_label(days[-1])}"),
            kpi_card("MARK_EMAIL_READ", "답변률", f"{rate}%", f"처리 완료 {done}건 · 확인 {acked}건"),
            kpi_card("LOCAL_FIRE_DEPARTMENT", "가장 많은 요청", top_cat, f"{ranked[0][1]}건" if ranked else ""),
            kpi_card("TODAY", "오늘 새 요청", f"{sum(1 for x in today_items if x['status'] == '접수')}건", "아직 답변 전"),
        ], spacing=12, run_spacing=12)
        mx = max(totals.values()) or 1
        rows = []
        for i, (c, n) in enumerate(ranked, 1):
            if n == 0:
                continue
            rows.append(ft.Row([
                ft.Container(txt(str(i), 12, WHITE if i <= 3 else MUTED, True), bgcolor=RED if i <= 3 else CARD_HI,
                             width=26, height=26, border_radius=13, alignment=ft.Alignment(0, 0)),
                ft.Icon(ic(CARE_ICON.get(c)), color=MUTED, size=18),
                ft.Container(txt(c, 13, TEXT, True), width=200),
                bar(n / mx, AMBER if i <= 3 else BAR_NEUTRAL, 220, 10),
                txt(f"{n}건 ({int(round(100 * n / total))}%)", 13, TEXT, True),
            ], spacing=10, vertical_alignment=CENTER))
        rank_card_c = card(ft.Column([section_title("LEADERBOARD", "요청 종류별 순위", "이번 주 많이 들어온 순")] + rows,
                                     spacing=10), col={"xs": 12, "lg": 7})
        dmx = max(daily) or 1
        day_bars = ft.Row([ft.Column([
            txt(str(n), 11, MUTED),
            ft.Container(width=26, height=max(3, int(90 * n / dmx)), bgcolor=AMBER if i == 6 else BAR_NEUTRAL,
                         border_radius=4),
            txt(KO_DAY[d.weekday()] + ("(오늘)" if i == 6 else ""), 11, MUTED),
        ], spacing=4, horizontal_alignment=CENTER) for i, (d, n) in enumerate(zip(days, daily))],
            spacing=14, vertical_alignment=ft.CrossAxisAlignment.END)
        hot, cold = totals.get("실내가 너무 더워요", 0), totals.get("실내가 너무 추워요", 0)
        if hot or cold:
            side = "더워요" if hot > cold else ("추워요" if cold > hot else None)
            temp_msg = (f"'{side}' 의견이 더 많아요 → " + (CARE_TIP["실내가 너무 더워요"] if side == "더워요"
                                                         else CARE_TIP["실내가 너무 추워요"])) if side else                 "의견이 반반이에요 → 구역별 온도 차이를 먼저 확인해 보세요."
        else:
            temp_msg = "이번 주 온도 관련 요청이 없어요."
        temp_card = ft.Container(bgcolor=CARD_HI, border_radius=12, padding=12, border=border_all(1, BORDER),
                                 content=ft.Column([
                                     txt("실내 온도 의견", 13, TEXT, True),
                                     ft.Row([chip(f"더워요 {hot}명", RED, RED_SOFT), chip(f"추워요 {cold}명", TEXT, BORDER)],
                                            spacing=8),
                                     txt(temp_msg, 12, MUTED),
                                 ], spacing=6))
        side_card = card(ft.Column([
            section_title("CALENDAR_MONTH", "요일별 요청 수", "최근 7일"), day_bars, temp_card,
        ], spacing=12), col={"xs": 12, "lg": 5})
        tips = [ft.Container(bgcolor=CARD_HI, border_radius=12, padding=12, border=border_all(1, BORDER),
                             content=ft.Column([
                                 ft.Row([ft.Icon(ic(CARE_ICON.get(c)), color=AMBER, size=18), txt(f"{c} · {n}건", 14, TEXT, True)],
                                        spacing=8),
                                 txt(CARE_TIP.get(c, ""), 13, MUTED),
                             ], spacing=4)) for c, n in ranked[:3] if n]
        tip_card = card(ft.Column([section_title("TIPS_AND_UPDATES", "이번 주 이렇게 케어해 보세요",
                                                 "많이 들어온 요청 TOP 3 대응 제안")] + tips, spacing=10))
        care_report_body.content = ft.Column([
            kpis, ft.ResponsiveRow([rank_card_c, side_card], spacing=16, run_spacing=16), tip_card,
            txt("※ 지난 6일은 시연용 가상 데이터, 오늘은 실제 요청함 기준", 11, MUTED),
        ], spacing=16)

    def open_care_report(e):
        care_rep["open"] = True
        rep["open"] = False
        render_care_report()
        apply_views()
        page.update()

    def close_care_report(e):
        care_rep["open"] = False
        apply_views()
        page.update()

    def render_all(update=True):
        with lock:
            render_admin()
            render_member()
            render_care()
        if update:
            page.update()

    # ---------------- 루틴 추천 ----------------
    def apply_preset(plist):
        for p, cb in part_checks.items():
            cb.value = p in plist
        page.update()

    def routine_item(i, part, eq_name, sc, free, qty, w, eid, est):
        label, color, soft = level(sc)
        sets = "3세트 x 10~12회" if part in BIG_PARTS else "3세트 x 12~15회"
        wait_txt = "대기 없음 · 바로 사용" if est == 0 else f"대기 {w}명 · 예상 대기 약 {est}분"
        return ft.Container(
            bgcolor=CARD_HI, border_radius=12, padding=12, border=border_all(1, BORDER),
            content=ft.Row([
                ft.Container(txt(str(i), 14, WHITE, True), bgcolor=RED, width=28, height=28, border_radius=14,
                             alignment=ft.Alignment(0, 0)),
                ft.Column([
                    ft.Row([txt(eq_name, 14, TEXT, True), part_tag(part), chip(f"{label} {sc}%", color, soft)],
                           spacing=6, wrap=True),
                    txt(f"{sets} · {wait_txt}", 12, MUTED),
                ], spacing=4, expand=True),
                btn("예약", on_click=lambda e, k=eid: on_reserve(k), height=34, width=80, kind="outline"),
            ], spacing=10, vertical_alignment=CENTER),
        )

    def on_reserve_all(e):
        ids = list(R["ids"])
        if not ids:
            toast("먼저 [맞춤 루틴 추천받기]를 눌러주세요", "warn")
            return
        done = 0
        with lock:
            for eid in ids:
                r = do_reserve(eid)
                if r and "error" not in r:
                    done += 1
                else:
                    break           # 예약 한도 도달
        render_all()
        if done == 0:
            toast(f"예약은 최대 {MAX_RESERVATIONS}개까지 가능해요. 먼저 사용 완료하거나 취소해 주세요", "warn")
        elif done < len(ids):
            toast(f"추천 {len(ids)}개 중 앞에서부터 {done}개만 예약했어요 · 예약은 최대 {MAX_RESERVATIONS}개까지예요", "warn")
        else:
            toast(f"추천 루틴 {done}개 기구를 모두 예약했어요 · 순서대로 알림을 드릴게요", "ok")

    def on_recommend(e):
        parts = [p for p in PART_ORDER if part_checks[p].value]
        if not parts:
            toast("운동할 부위를 1개 이상 선택해 주세요", "warn")
            return
        picks, missing, saved, total_wait = [], [], 0, 0
        with lock:
            per = 2 if len(parts) <= 2 else 1
            for p in parts:
                cands = [x for x in S["equipments"] if x["part"] == p]
                if not cands:
                    missing.append(p)
                    continue
                cands.sort(key=lambda x: (congestion(x), -free_units(x), waiting_total(x)))
                chosen = [cands[0]] + [c for c in cands[1:per] if congestion(c) < 70]
                worst = max(est_wait_min(c) for c in cands)
                for c in chosen:
                    est = est_wait_min(c)
                    picks.append((p, c["name"], congestion(c), free_units(c), c["qty"], waiting_total(c), c["id"], est))
                    saved += max(0, worst - est)
                    total_wait += est
        routine_col.controls.clear()
        R["ids"] = [x[6] for x in picks]
        if not picks:
            routine_col.controls.append(txt("선택한 부위에 등록된 기구가 없어요. 관장용 탭에서 기구를 등록해 주세요.", 13, MUTED))
            page.update()
            toast("선택한 부위에 등록된 기구가 없어요", "warn")
            return
        total_min = len(picks) * 10 + total_wait
        summary = [txt(f"총 {len(picks)}개 기구 · 예상 소요 약 {total_min}분 (운동 {len(picks) * 10}분 + 대기 {total_wait}분)",
                       13, TEXT, True)]
        if saved > 0:
            summary.append(txt(f"혼잡한 기구를 피해 예상 대기 {saved}분을 아꼈어요", 12, GREEN, True))
        summary.append(txt("초심자 팁: 가벼운 무게로 자세부터, 세트 사이 휴식은 60~90초", 12, MUTED))
        if len(picks) > MAX_RESERVATIONS:
            summary.append(txt(f"예약은 최대 {MAX_RESERVATIONS}개까지라 [전체 예약]은 앞에서부터 {MAX_RESERVATIONS}개만 담아요",
                               12, AMBER, True))
        routine_col.controls.append(ft.Container(
            bgcolor=RED_SOFT, border=border_all(1, RED), border_radius=12, padding=12,
            content=ft.Column([ft.Row([ft.Icon(ic("AUTO_AWESOME"), color=RED, size=18),
                                       txt("오늘의 맞춤 루틴", 14, RED, True)], spacing=6)] + summary, spacing=4)))
        for i, (p, name, sc, fr, qty, w, eid, est) in enumerate(picks, 1):
            routine_col.controls.append(routine_item(i, p, name, sc, fr, qty, w, eid, est))
        if missing:
            routine_col.controls.append(txt(f"등록된 기구가 없는 부위: {', '.join(missing)}", 12, MUTED))
        routine_col.controls.append(btn("추천 기구 전체 예약", on_click=on_reserve_all, icon=ic("EVENT_AVAILABLE"), height=42))
        page.update()
        toast(f"혼잡도가 낮은 기구 {len(picks)}개로 루틴을 구성했어요", "ok")

    for _p in PARTS:
        part_checks[_p] = mk(ft.Checkbox, label=_p, value=False, active_color=RED, check_color=WHITE,
                             label_style=ft.TextStyle(color=TEXT))

    def preset_chip(label, plist):
        return ft.Container(content=txt(label, 12, RED, True), bgcolor=RED_SOFT, border_radius=14,
                            padding=pad(12, 6), on_click=lambda e, pl=plist: apply_preset(pl))

    # ======================================================================
    # 관장용: 주간 혼잡도 리포트 (7일 일별 데이터 + 증설/처분 추천)
    # ======================================================================
    rep = {"open": False, "tab": 0}      # tab 0 = 증설/처분 추천, 1~7 = 최근 7일(오래된 날 -> 오늘)
    report_tabs = ft.Row(wrap=True, spacing=8, run_spacing=8)
    report_body = ft.Container()
    report_period = txt("", 12, MUTED)

    def day_label(d):
        return f"{d.month:02d}/{d.day:02d}({KO_DAY[d.weekday()]})"

    def spark(vals, days):
        """7일치 혼잡도를 작은 막대 7개로 보여준다 (막대에 마우스를 올리면 날짜와 값)"""
        cols = []
        for v, d in zip(vals, days):
            _, color, _ = level(v)
            cols.append(ft.Column([
                ft.Container(width=12, height=44, alignment=ft.Alignment(0, 1), tooltip=f"{day_label(d)} {v}%",
                             content=ft.Container(width=12, height=max(3, int(44 * v / 100)), bgcolor=color,
                                                  border_radius=3)),
                txt(KO_DAY[d.weekday()], 9, MUTED),
            ], spacing=2, horizontal_alignment=CENTER))
        return ft.Row(cols, spacing=5)

    def reco_item(r, days, expand):
        eq, st, qty = r["eq"], r["stats"], r["eq"]["qty"]
        lines = []
        if expand:
            act = chip(f"증설 +{r['add']}대", WHITE, RED)
            tag = chip("긴급", RED, RED_SOFT) if r["urgent"] else None
            to_qty = qty + r["add"]
            effect = f"주 평균 혼잡도 {st['avg']}% → {r['after']}%"
            accent = RED if r["urgent"] else BORDER
            lines = [txt(f"회원 의견 '대기가 너무 길어요' 최근 7일 {long_wait_week(eq)}건", 12, MUTED)]
        else:
            act = chip(f"처분 -{r['remove']}대", AMBER, AMBER_SOFT)
            tag = chip("우선", AMBER, AMBER_SOFT) if r["urgent"] else None
            to_qty = qty - r["remove"]
            effect = (f"주 평균 혼잡도 {st['avg']}% → {r['after']}%" if r["after"] is not None
                      else f"주 평균 혼잡도 {st['avg']}% · 처분 후 다른 기구로 교체 검토")
            accent = AMBER if r["urgent"] else BORDER
        head = [txt(eq["name"], 15, TEXT, True), part_tag(eq["part"]), act] + ([tag] if tag is not None else [])
        return ft.Container(
            bgcolor=CARD_HI, border_radius=12, padding=14, border=border_all(1, accent),
            content=ft.Column([
                ft.Row(head, spacing=8, wrap=True),
                txt(f"보유 {qty}대 → {to_qty}대 · {effect}", 13, TEXT, True),
            ] + lines, spacing=6),
        )

    def build_reco_tab(eqs, days):
        expand, dispose, keep = recommend(eqs, days)
        summary = ft.ResponsiveRow([
            kpi_card("TRENDING_UP", "증설 추천", f"{len(expand)}종", f"총 +{sum(r['add'] for r in expand)}대 필요"),
            kpi_card("TRENDING_DOWN", "처분(감축) 추천", f"{len(dispose)}종", f"총 -{sum(r['remove'] for r in dispose)}대 여유"),
            kpi_card("CHECK_CIRCLE_OUTLINE", "현 수량 유지", f"{len(keep)}종", "적정 수준"),
            kpi_card("CALENDAR_MONTH", "분석 기간", "최근 7일", f"{day_label(days[0])} ~ {day_label(days[-1])}"),
        ], spacing=12, run_spacing=12)
        exp_items = [reco_item(r, days, True) for r in expand] or [txt("증설이 필요한 기구가 없어요", 13, MUTED)]
        dis_items = [reco_item(r, days, False) for r in dispose] or [txt("처분을 검토할 기구가 없어요", 13, MUTED)]
        exp_card = card(ft.Column([section_title("TRENDING_UP", "증설 추천 기구", "주 평균 혼잡도 높은 순")]
                                  + exp_items, spacing=10), col={"xs": 12, "lg": 6})
        dis_card = card(ft.Column([section_title("TRENDING_DOWN", "처분(감축) 추천 기구", "주 평균 혼잡도 낮은 순")]
                                  + dis_items, spacing=10), col={"xs": 12, "lg": 6})
        note = txt(f"※ 판단 기준(최근 7일 주 평균 혼잡도) · 증설: {EXPAND_AVG}% 이상 / "
                   f"처분: {DISPOSE_AVG}% 이하이면서 1대 줄여도 {DISPOSE_AFTER_MAX}% 이하 (시연용 더미 데이터 기반)", 11, MUTED)
        return ft.Column([summary, ft.ResponsiveRow([exp_card, dis_card], spacing=16, run_spacing=16), note],
                         spacing=16)

    def build_day_tab(eqs, d, days):
        today = datetime.now().date()
        expand, dispose, _ = recommend(eqs, days)
        verdict = {r["eq"]["name"]: "expand" for r in expand}
        verdict.update({r["eq"]["name"]: "dispose" for r in dispose})
        data = sorted(((eq, day_congestion(eq, d)) for eq in eqs), key=lambda x: -x[1])
        scores = [s for _, s in data]
        avg = int(round(sum(scores) / len(scores))) if scores else 0
        prev = [day_congestion(eq, d - timedelta(days=1)) for eq in eqs]
        diff = avg - (int(round(sum(prev) / len(prev))) if prev else 0)
        top_name, top_sc = (data[0][0]["name"], data[0][1]) if data else ("-", 0)
        arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "-")
        kpis = ft.ResponsiveRow([
            kpi_card("SPEED", "일 평균 혼잡도", f"{avg}%", f"전일 대비 {arrow}{abs(diff)}%p" if diff else "전일과 동일"),
            kpi_card("LOCAL_FIRE_DEPARTMENT", "가장 붐빈 기구", top_name, f"혼잡도 {top_sc}%"),
            kpi_card("WARNING_AMBER", "혼잡 기구 (70%↑)", f"{sum(1 for s in scores if s >= 70)}종", f"전체 {len(scores)}종 중"),
            kpi_card("EVENT_AVAILABLE", "여유 기구 (40%↓)", f"{sum(1 for s in scores if s < 40)}종", "바로 이용 가능"),
        ], spacing=12, run_spacing=12)
        rows = []
        for eq, sc in data:
            label, color, soft = level(sc)
            v = verdict.get(eq["name"])
            vcell = (chip("증설 추천", WHITE, RED) if v == "expand"
                     else chip("처분 추천", AMBER, AMBER_SOFT) if v == "dispose" else txt("-", 13, MUTED))
            rows.append(ft.DataRow(cells=[
                ft.DataCell(txt(eq["name"], 14, TEXT, True)),
                ft.DataCell(part_tag(eq["part"])),
                ft.DataCell(txt(f"{eq['qty']}대", 14, TEXT)),
                ft.DataCell(ft.Row([bar(sc / 100, color, 140), txt(f"{sc}%", 13, color, True), chip(label, color, soft)],
                                   spacing=8)),
                ft.DataCell(txt(day_peak_slot(eq, d), 14, TEXT)),
                ft.DataCell(txt(f"{wait_minutes(sc)}분", 14, RED if sc >= 70 else (AMBER if sc >= 40 else GREEN), True)),
                ft.DataCell(vcell),
            ]))
        heads = ["기구", "부위", "보유", "하루 평균 혼잡도", "피크 시간대", "평균 대기", "주간 판단"]
        table = mk(ft.DataTable, columns=[ft.DataColumn(txt(h, 12, MUTED, True)) for h in heads], rows=rows,
                   heading_row_color=CARD_HI, divider_thickness=0.5, column_spacing=28)
        title = day_label(d) + (" · 오늘" if d == today else "")
        return ft.Column([
            kpis,
            card(ft.Column([
                section_title("QUERY_STATS", f"{title} 기구별 혼잡도", "혼잡도 높은 순 · '주간 판단'은 최근 7일 데이터 기준"),
                ft.Row([table], scroll=ft.ScrollMode.AUTO),
            ], spacing=10)),
        ], spacing=16)

    def render_report():
        today = datetime.now().date()
        days = week_days(today)
        report_period.value = f"{day_label(days[0])} ~ {day_label(days[-1])} · 최근 7일"
        tabs = [("증설 / 처분 추천", "AUTO_AWESOME")] + [(day_label(d) + (" · 오늘" if d == today else ""), None)
                                                     for d in days]
        report_tabs.controls.clear()
        for i, (label, icon) in enumerate(tabs):
            sel = rep["tab"] == i
            items = ([ft.Icon(ic(icon), color=WHITE if sel else RED, size=16)] if icon else []) + \
                    [txt(label, 13, WHITE if sel else MUTED, True)]
            report_tabs.controls.append(ft.Container(
                content=ft.Row(items, spacing=6, tight=True), bgcolor=RED if sel else CARD,
                border=border_all(1, RED if sel else BORDER), border_radius=10, padding=pad(14, 9),
                on_click=lambda e, i=i: select_report_tab(i)))
        with lock:
            eqs = [dict(e) for e in S["equipments"]]
        report_body.content = (build_reco_tab(eqs, days) if rep["tab"] == 0
                               else build_day_tab(eqs, days[rep["tab"] - 1], days))

    def select_report_tab(i):
        rep["tab"] = i
        render_report()
        page.update()

    def apply_views():
        """관장용 탭 안에서는 '대시보드' 또는 '주간 리포트' 중 하나만, 회원용 탭에서는 회원 화면만 보인다."""
        admin_view.visible = S["tab"] == 0 and not rep["open"] and not care_rep["open"]
        report_view.visible = S["tab"] == 0 and rep["open"]
        care_report_view.visible = S["tab"] == 0 and care_rep["open"]
        member_view.visible = S["tab"] == 1
        care_view.visible = S["tab"] == 2

    def open_report(e):
        rep.update(open=True, tab=0)
        care_rep["open"] = False
        render_report()
        apply_views()
        page.update()

    def close_report(e):
        rep["open"] = False
        apply_views()
        page.update()

    # ======================================================================
    # 화면 조립
    # ======================================================================
    hours_text.value = f"영업시간  {S['gym']['open']} ~ {S['gym']['close']}"

    header = ft.Row([
        ft.Row([
            ft.Container(ft.Icon(ic("FITNESS_CENTER"), color=WHITE, size=28), bgcolor=RED, padding=10, border_radius=14),
            ft.Column([
                ft.Row([txt("Heal", 30, WHITE, True), txt("F", 30, RED, True)], spacing=0),
                txt("헬스 프렌드 · 눈치 보지 말고, 비어있는 기구로", 12, MUTED),
            ], spacing=0),
        ], spacing=12),
        ft.Container(
            content=ft.Row([ft.Icon(ic("ACCESS_TIME"), color=RED, size=16), hours_text], spacing=6),
            bgcolor=CARD, border=border_all(1, BORDER), border_radius=20, padding=pad(14, 8)),
    ], alignment=BETWEEN, vertical_alignment=CENTER, wrap=True)

    # ---- 커스텀 탭 바 (Flet 버전과 무관하게 동작) ----
    tab_row = ft.Row(spacing=8)

    def render_tabs():
        tab_row.controls.clear()
        for i, (label, icon) in enumerate([("관장용 탭", "ADMIN_PANEL_SETTINGS"), ("회원용 탭", "PERSON"),
                                           ("케어 요청 (회원)", "SUPPORT_AGENT")]):
            sel = S["tab"] == i
            tab_row.controls.append(ft.Container(
                content=ft.Row([ft.Icon(ic(icon), color=WHITE if sel else MUTED, size=18),
                                txt(label, 14, WHITE if sel else MUTED, True)], spacing=8, tight=True),
                bgcolor=RED if sel else CARD, border=border_all(1, RED if sel else BORDER),
                border_radius=12, padding=pad(24, 12), on_click=lambda e, i=i: switch_tab(i)))

    def switch_tab(i):
        S["tab"] = i
        apply_views()
        render_tabs()
        page.update()

    # ---- 관장용 화면 ----
    form_card = card(ft.Column([
        section_title("ADD_CIRCLE_OUTLINE", "기구 · 영업시간 등록", "헬스장에 기구를 등록해 보세요"),
        f_name,
        txt("대상 부위", 12, MUTED, True),
        part_row,
        f_qty,
        ft.Row([btn("기구 등록", on_click=on_register, icon=ic("ADD"), width=180)]),
        ft.Divider(color=BORDER, height=18),
        txt("헬스장 영업시간", 12, MUTED, True),
        ft.Row([f_open, txt("~", 18, MUTED, True), f_close], spacing=10, vertical_alignment=CENTER),
        ft.Row([btn("영업시간 저장", on_click=on_save_hours, icon=ic("SAVE"), kind="outline", width=180)]),
        ft.Divider(color=BORDER, height=18),
        btn("시연 데이터 초기화", on_click=on_reset, icon=ic("RESTART_ALT"), kind="ghost", height=36),
    ], spacing=12), col={"xs": 12, "lg": 4})

    cong_card = card(ft.Column([
        section_title("INSIGHTS", "전체 기구 현재 혼잡도", None,
                      trailing=btn("혼잡도 새로고침", on_click=on_simulate, icon=ic("REFRESH"), kind="ghost", height=36)),
        cong_sub,
        cong_holder,
    ], spacing=10), col={"xs": 12, "lg": 8})

    peak_card = card(ft.Column([
        section_title("LOCAL_FIRE_DEPARTMENT", "오늘의 피크 타임", "가장 붐빈 시간대와 그때 기구별 대기"),
        trend_holder,
    ], spacing=12), col={"xs": 12, "lg": 5})
    rank_card = card(ft.Column([
        section_title("LEADERBOARD", "오늘 기구 이용 순위", "이용 고객 수 · 평균 대기 (시연용 가상 데이터)"),
        rank_holder,
    ], spacing=12), col={"xs": 12, "lg": 7})
    trend_card = ft.ResponsiveRow([peak_card, rank_card], spacing=16, run_spacing=16)

    report_btn = btn("주간 혼잡도 리포트 · 증설/처분 추천", on_click=open_report, icon=ic("ANALYTICS"), height=46)
    admin_top = ft.Row([
        ft.Column([txt("관장 대시보드", 20, TEXT, True),
                   txt("기구 등록 · 현재 혼잡도 · 주간 분석을 한곳에서 확인하세요", 12, MUTED)], spacing=0),
        report_btn,
    ], alignment=BETWEEN, vertical_alignment=CENTER, wrap=True)

    care_inbox_card = card(ft.Column([
        section_title("SUPPORT_AGENT", "오늘의 회원 케어 요청함", "이탈로 이어지기 전에 작은 불편부터 챙겨요",
                      trailing=btn("이번 주 분석 보기", on_click=open_care_report, icon=ic("ANALYTICS"), kind="outline",
                                   height=38)),
        care_kpi,
        txt("확인했어요: 바로 처리하기 어렵거나 의견이 갈릴 때 · 답변 쓰기: 직접 답변 · 처리 완료: 조치를 마쳤을 때", 11, MUTED),
        care_inbox_col,
    ], spacing=10))

    care_report_view = ft.Column([
        ft.Row([
            btn("관장 화면으로", on_click=close_care_report, icon=ic("ARROW_BACK"), kind="ghost", height=42, width=164),
            ft.Column([txt("이번 주 회원 케어 분석", 20, TEXT, True),
                       txt("어떤 불편이 많았는지 보고 먼저 챙길 것을 정해요", 12, MUTED)], spacing=0),
        ], spacing=14, vertical_alignment=CENTER),
        care_report_body,
    ], spacing=16, visible=False)

    admin_view = ft.Column([admin_top, kpi_row, ft.ResponsiveRow([form_card, cong_card], spacing=16, run_spacing=16),
                            care_inbox_card, trend_card], spacing=16, visible=True)

    care_view = ft.Column([
        card(ft.Column([
            section_title("SUPPORT_AGENT", "관장님께 요청하기", "운동하며 불편했던 점을 익명으로 알려주세요"),
            care_cat_row,
            care_detail,
            ft.Row([btn("요청 보내기", on_click=on_send_care, icon=ic("SEND"), width=160, height=44),
                    txt("같은 종류의 요청은 하루 한 번 · 이름 없이 전달돼요", 12, MUTED)], spacing=12,
                   vertical_alignment=CENTER, wrap=True),
        ], spacing=14)),
        card(ft.Column([section_title("HISTORY", "내 요청 현황", "처리되면 관장님 답변이 여기에 표시돼요"),
                        care_mine_col], spacing=10)),
    ], spacing=16, visible=False)

    report_view = ft.Column([
        ft.Row([
            btn("관장 화면으로", on_click=close_report, icon=ic("ARROW_BACK"), kind="ghost", height=42, width=164),
            ft.Column([txt("주간 혼잡도 리포트", 20, TEXT, True), report_period], spacing=0),
        ], spacing=14, vertical_alignment=CENTER),
        report_tabs,
        report_body,
    ], spacing=16, visible=False)

    # ---- 회원용 화면 ----
    equip_card_box = card(ft.Column([
        section_title("FITNESS_CENTER", "기구 현황 · 예약", "비어있는 기구를 확인하고 바로 예약하세요",
                      trailing=btn("혼잡도 새로고침", on_click=on_simulate, icon=ic("REFRESH"), kind="ghost", height=36)),
        mpart_row,
        top_strip,
        grid,
    ], spacing=12), col={"xs": 12, "lg": 7})

    ticket_card = card(ft.Column([
        section_title("TIMER", "내 예약 현황", f"최대 {MAX_RESERVATIONS}개까지 예약 · 순서가 되면 팝업으로 알려드려요",
                      trailing=ticket_badge),
        ticket_col,
    ], spacing=10))

    routine_card = card(ft.Column([
        section_title("AUTO_AWESOME", "초심자 맞춤 루틴 추천", "오늘 운동할 부위를 고르면 한산한 기구로 짜드려요"),
        ft.Row(list(part_checks.values()), wrap=True, spacing=4, run_spacing=0),
        ft.Row([txt("빠른 선택", 12, MUTED)] + [preset_chip("가슴 + 삼두", ["가슴", "삼두"]),
                                              preset_chip("등 + 이두", ["등", "이두"]),
                                              preset_chip("하체 + 어깨", ["하체", "어깨"])],
               spacing=8, wrap=True, vertical_alignment=CENTER),
        btn("맞춤 루틴 추천받기", on_click=on_recommend, icon=ic("AUTO_AWESOME"), height=44),
        routine_col,
    ], spacing=12))

    member_view = ft.Column([
        live_banner,
        ft.ResponsiveRow([equip_card_box,
                          ft.Container(col={"xs": 12, "lg": 5}, content=ft.Column([ticket_card, routine_card], spacing=16))],
                         spacing=16, run_spacing=16),
    ], spacing=16, visible=False)

    footer = txt("※ 과제 시연용 목업입니다. 모든 데이터는 가상이며, 알림까지의 대기시간은 시연을 위해 가속되어 있어요. "
                 f"(사용 가능 알림 후 응답 제한: {fmt_dur(READY_TIMEOUT_SEC)} · 기구 1회 최대 이용: {fmt_dur(MAX_USE_SEC)})", 11, MUTED)

    page.add(header, ft.Container(height=4), tab_row, ft.Container(height=2), admin_view, report_view, care_report_view, member_view, care_view,
             footer)
    render_tabs()
    render_part_chips()
    render_all()

    # ---------------- 백그라운드 타이머 (1초 주기) ----------------
    def ticker():
        fails = 0
        while True:
            time.sleep(1)
            try:
                fired_ids, expired, expired_ids, ended = [], [], [], []
                with lock:
                    now = time.time()
                    for t in list(S["tickets"]):
                        if t["status"] == "timing" and now >= t["ready_at"]:
                            fire(t)                                   # 내 차례 -> 사용 가능
                            fired_ids.append(t["id"])
                        elif t["status"] == "ready" and now >= t["expire_at"]:
                            expired.append(eq_name(t))                # 제한시간 내 무응답 -> 자동 취소
                            expired_ids.append(t["id"])
                            cancel_ticket(t)
                        elif t["status"] == "using" and now >= t["end_at"]:
                            ended.append(eq_name(t))                  # 최대 이용시간 경과 -> 자동 사용 종료
                            finish_ticket(t)
                    active = any(t["status"] in ("timing", "ready", "using") for t in S["tickets"])
                for tid in expired_ids:
                    close_popup(tid)
                if fired_ids or expired or ended:
                    render_all()
                    msgs = []
                    if expired:
                        msgs.append(f"'{', '.join(expired)}' 예약이 {fmt_dur(READY_TIMEOUT_SEC)} 동안 응답이 없어 자동 취소됐어요")
                    if ended:
                        msgs.append(f"'{', '.join(ended)}' 최대 이용시간({fmt_dur(MAX_USE_SEC)})을 넘겨 자동으로 사용 종료됐어요")
                    if msgs:
                        toast(" / ".join(msgs), "warn")
                    maybe_show_popup()
                    with lock:
                        pending = [eq_name(x) for x in S["tickets"]
                                   if x["id"] in fired_ids and x["status"] == "ready" and not x["popup_shown"]]
                        cur = using_ticket()
                        cur_name = eq_name(cur) if cur is not None else None
                    if pending and cur_name and not msgs:
                        # 이미 다른 기구를 사용 중이라 팝업 없이 목록으로만 안내
                        # (다른 팝업이 열려 있어 보류된 건은 팝업이 닫힌 뒤 자동으로 이어서 뜨므로 안내 불필요)
                        toast(f"'{', '.join(pending)}' 사용 가능! 하지만 '{cur_name}'을(를) 사용 중이라 "
                              f"[사용 완료] 후에 이용할 수 있어요", "warn")
                elif active:
                    refresh_live()
                    page.update()
                fails = 0
            except Exception:
                fails += 1
                if fails > 5:   # 창이 닫힌 경우 등
                    break

    threading.Thread(target=ticker, daemon=True).start()


if __name__ == "__main__":
    # python healf.py        -> 데스크톱 창으로 실행
    # python healf.py web    -> 브라우저로 실행 (데스크톱 클라이언트 설치가 막힌 PC용)
    import sys

    kwargs = {"view": ft.AppView.WEB_BROWSER} if "web" in sys.argv[1:] else {}
    if hasattr(ft, "run"):
        ft.run(main, **kwargs)
    else:
        ft.app(target=main, **kwargs)
