"""M1/M2 — 처방·토큰·오프라인 발급 구현 모델.

구현 충실도 원칙: 이 모듈은 spec이 아니라 **코드가 실제로 하는 일**을 모델링한다.
(근거 라인은 traceability.md 참조: server/app/db.py create_prescription/edit_prescription/
reissue/_issue_token_tx/claim_first_view, server/app/routers/api.py, patient.py)

spec과의 차이가 곧 반례다:
- create replay는 본문 해시를 비교하지 않는다(api.py에는 IDEMPOTENCY_CONFLICT 발생 경로 없음).
- edit_prescription은 revoked만 거부하고 만료는 거부하지 않으며, expires_at을 재산정한다(db.py:1544-1549).
- reissue는 구건 status·revoked_at을 무가드 UPDATE한다(db.py:1443-1449).

동시성 모델: SQLite 단일 writer 직렬화를 전제로 각 API 호출을
  begin(트랜잭션 밖 읽기·판정) → commit(원자 쓰기, UNIQUE 충돌 시 IntegrityError 경로)
2단계로 나눈다. 트랜잭션 자체는 원자(부분 상태 없음 — create/edit/reissue 모두
단일 conn.commit()), 크래시는 "커밋 후 응답 유실 → 재시도"로 환원된다.

시간: 정수 클록. TTL은 duration 짧음=3, 김=6으로 축약(D6 공식의 단조성만 보존).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from explorer import Action, ExplorationResult, explore, format_violation  # noqa: E402

# --fixed: 2026-07-11 수정 후 의미론(만료 편집 410, revoked reissue 410, replay 본문 대조)
FIXED = "--fixed" in sys.argv

MAX_CLOCK = 9
TTL = {"short": 3, "long": 7}
PURGE_GRACE = 2

# ---------------------------------------------------------------------------
# 상태
# ---------------------------------------------------------------------------
# rx[i]      = {"status": "active"|"revoked", "version": int, "cid": str,
#               "body": str, "reissue_of": int|None}
# tok[i]     = {"rx": int, "created": int, "expires": int,
#               "revoked_at": int|None, "first_viewed": int|None}   (rx i와 1:1, 같은 인덱스)
# revisions  = rx별 append 횟수 (원장 단조 증가 확인용)
# ev_created / ev_revoked = rx별 이벤트 수 (INV-5/6·감사 중복)
# flags      = 위반 관측 플래그(모델이 관측한 순간 기록):
#   replay_body_mismatch  — 동일 멱등키+다른 본문이 409 아닌 성공 replay로 흡수됨 (T1c 계약)
#   revoked_at_overwrite  — 비NULL revoked_at이 다른 값으로 덮어써짐 (INV-10)
#   binding_changed       — token→rx 바인딩 변경 (INV-1a)
# ever_dead[i] — 파생 상태가 revoked/expired로 관측된 적 있는 토큰 (INV-2용 이력)


def initial_state() -> dict:
    return {
        "clock": 0,
        "rx": [],
        "tok": [],
        "revisions": [],
        "ev_created": [],
        "ev_revoked": [],
        "ever_dead": [],
        "flags": {
            "replay_body_mismatch": False,
            "revoked_at_overwrite": False,
            "binding_changed": False,
        },
        # 진행 중 요청(begin됐지만 commit 안 됨): (kind, cid, body, target_rx)
        "inflight": [],
    }


def derived_status(state: dict, i: int) -> str:
    """db._token_status와 동일 판정 (db.py:1315-1321)."""
    tok, rx = state["tok"][i], state["rx"][i]
    if tok["revoked_at"] is not None or rx["status"] == "revoked":
        return "revoked"
    if tok["expires"] <= state["clock"]:
        return "expired"
    return "active"


def observe(state: dict) -> dict:
    """전이 직후 파생 상태 관측 — INV-2의 이력(ever_dead) 갱신."""
    for i in range(len(state["tok"])):
        if derived_status(state, i) in ("revoked", "expired"):
            state["ever_dead"] = list(state["ever_dead"])
            state["ever_dead"][i] = True
    return state


def _copy(state: dict) -> dict:
    return {
        "clock": state["clock"],
        "rx": [dict(r) for r in state["rx"]],
        "tok": [dict(t) for t in state["tok"]],
        "revisions": list(state["revisions"]),
        "ev_created": list(state["ev_created"]),
        "ev_revoked": list(state["ev_revoked"]),
        "ever_dead": list(state["ever_dead"]),
        "flags": dict(state["flags"]),
        "inflight": [tuple(x) for x in state["inflight"]],
    }


# ---------------------------------------------------------------------------
# 전이 (구현 의미론)
# ---------------------------------------------------------------------------

def commit_create(state: dict, cid: str, body: str) -> dict:
    """db.create_prescription — 단일 트랜잭션. UNIQUE(cid) 선재 시 replay(쓰기 0)."""
    s = _copy(state)
    for i, r in enumerate(s["rx"]):
        if r["cid"] == cid:
            if r["body"] != body:
                if FIXED:
                    return observe(s)  # 409 IDEMPOTENCY_CONFLICT — 쓰기 0 (db._replay_or_conflict)
                # 수정 전: 본문 비교 없이 기존 결과 반환 (구 db.py:1226-1228)
                s["flags"]["replay_body_mismatch"] = True
            return observe(s)
    i = len(s["rx"])
    s["rx"].append({"status": "active", "version": 1, "cid": cid,
                    "body": body, "reissue_of": None})
    s["tok"].append({"rx": i, "created": s["clock"],
                     "expires": s["clock"] + TTL[body],
                     "revoked_at": None, "first_viewed": None})
    s["revisions"].append(0)
    s["ev_created"].append(1)
    s["ev_revoked"].append(0)
    s["ever_dead"].append(False)
    return observe(s)


def commit_edit(state: dict, i: int, body: str) -> dict:
    """db.edit_prescription — revoked만 거부(1500-1502), 만료 가드 없음.
    expires_at 재산정: token.created + TTL(new body) (1544-1549)."""
    s = _copy(state)
    rx = s["rx"][i]
    if rx["status"] == "revoked":
        return observe(s)  # ValueError → 410, 쓰기 0
    if FIXED and derived_status(s, i) != "active":
        return observe(s)  # PrescriptionExpiredError → 410, 쓰기 0 (만료 가드 + 조건부 UPDATE)
    rx["version"] += 1
    rx["body"] = body
    s["revisions"][i] += 1
    s["tok"][i]["expires"] = s["tok"][i]["created"] + TTL[body]
    return observe(s)


def commit_reissue(state: dict, i: int, cid: str) -> dict:
    """db.reissue — 구건 무가드 revoke(1443-1449) + 신규 발급, 단일 트랜잭션.
    라우터 replay 선검사(api.py:359-363)는 begin 단계에서 모델링되지만,
    여기서는 UNIQUE(cid) 충돌 → replay(쓰기 0)로 흡수한다(api.py:372-379)."""
    s = _copy(state)
    for r in s["rx"]:
        if r["cid"] == cid:
            return observe(s)  # IntegrityError → replay
    if FIXED and (
        s["tok"][i]["revoked_at"] is not None or s["rx"][i]["status"] == "revoked"
    ):
        return observe(s)  # ValueError → 410 LINK_REVOKED, 쓰기 0 (조건부 UPDATE 백스톱 포함)
    old_tok = s["tok"][i]
    if old_tok["revoked_at"] is not None:
        # UPDATE ... SET revoked_at = now (무조건) — 비NULL 덮어쓰기 관측
        if old_tok["revoked_at"] != s["clock"]:
            s["flags"]["revoked_at_overwrite"] = True
    s["rx"][i]["status"] = "revoked"
    old_tok["revoked_at"] = s["clock"]
    s["revisions"][i] += 1
    s["ev_revoked"][i] += 1
    j = len(s["rx"])
    s["rx"].append({"status": "active", "version": 1, "cid": cid,
                    "body": s["rx"][i]["body"], "reissue_of": i})
    s["tok"].append({"rx": j, "created": s["clock"],
                     "expires": s["clock"] + TTL[s["rx"][i]["body"]],
                     "revoked_at": None, "first_viewed": None})
    s["revisions"].append(0)
    s["ev_created"].append(1)
    s["ev_revoked"].append(0)
    s["ever_dead"].append(False)
    return observe(s)


def view(state: dict, i: int) -> dict:
    """GET /p/{token} — active면 first_viewed 원자 선점(claim_first_view),
    전 경로 단일 커밋(patient.py:459-475)."""
    s = _copy(state)
    if derived_status(s, i) == "active" and s["tok"][i]["first_viewed"] is None:
        s["tok"][i]["first_viewed"] = s["clock"]
    return observe(s)


def tick(state: dict) -> dict:
    s = _copy(state)
    s["clock"] += 1
    return observe(s)


# ---------------------------------------------------------------------------
# 액션 생성 (유한 경계)
# ---------------------------------------------------------------------------

CIDS = ("c1", "c2", "c3")
BODIES = ("short", "long")


def build_actions() -> list[Action]:
    acts: list[Action] = []
    acts.append(Action("tick", lambda s: s["clock"] < MAX_CLOCK, tick))

    for cid in CIDS[:2]:          # 신규 발급은 c1, c2로 제한
        for body in BODIES:
            acts.append(Action(
                f"create({cid},{body})",
                lambda s, c=cid: len(s["rx"]) < 3,
                lambda s, c=cid, b=body: commit_create(s, c, b),
            ))
    for i in range(3):
        for body in BODIES:
            acts.append(Action(
                f"edit(rx{i},{body})",
                lambda s, i=i: i < len(s["rx"]) and s["rx"][i]["version"] < 3,
                lambda s, i=i, b=body: commit_edit(s, i, b),
            ))
        acts.append(Action(
            f"reissue(rx{i},c3)",
            lambda s, i=i: i < len(s["rx"]) and len(s["rx"]) < 3,
            lambda s, i=i: commit_reissue(s, i, "c3"),
        ))
        # 이중탭/재전송: 서로 다른 cid 두 개로 같은 rx를 reissue (c2를 재사용해 경계 유지)
        acts.append(Action(
            f"reissue(rx{i},c2)",
            lambda s, i=i: i < len(s["rx"]) and len(s["rx"]) < 3,
            lambda s, i=i: commit_reissue(s, i, "c2"),
        ))
        acts.append(Action(
            f"view(rx{i})",
            lambda s, i=i: i < len(s["tok"]),
            lambda s, i=i: view(s, i),
        ))
    return acts


# ---------------------------------------------------------------------------
# 불변식
# ---------------------------------------------------------------------------

def inv2_no_resurrection(s: dict) -> bool:
    """INV-2: revoked/expired로 한 번 관측된 토큰이 다시 active가 되지 않는다."""
    for i in range(len(s["tok"])):
        if s["ever_dead"][i] and derived_status(s, i) == "active":
            return False
    return True


def inv5_no_duplicate_issue(s: dict) -> bool:
    """INV-5: 멱등키당 rx ≤ 1, rx.created는 rx당 정확히 1."""
    cids = [r["cid"] for r in s["rx"]]
    if len(cids) != len(set(cids)):
        return False
    return all(c == 1 for c in s["ev_created"])


def inv10_provenance(s: dict) -> bool:
    """INV-10: revoked_at 비NULL 덮어쓰기 없음, rx.revoked 이벤트 rx당 ≤ 1."""
    if s["flags"]["revoked_at_overwrite"]:
        return False
    return all(c <= 1 for c in s["ev_revoked"])


def inv1_binding(s: dict) -> bool:
    """INV-1a: token→rx 바인딩 불변 + 1:1."""
    if s["flags"]["binding_changed"]:
        return False
    return all(s["tok"][i]["rx"] == i for i in range(len(s["tok"])))


def contract_t1c_idempotency_conflict(s: dict) -> bool:
    """T1c(01 §4.1): 동일 멱등키+다른 본문은 409여야 한다 — 성공 replay로 흡수되면 위반."""
    return not s["flags"]["replay_body_mismatch"]


INVARIANTS = {
    "INV-2:no-resurrection": inv2_no_resurrection,
    "INV-5:no-duplicate-issue": inv5_no_duplicate_issue,
    "INV-10:immutable-provenance": inv10_provenance,
    "INV-1:token-binding": inv1_binding,
    "T1c:idempotency-conflict-contract": contract_t1c_idempotency_conflict,
}


def run(max_states: int = 1_500_000) -> ExplorationResult:
    return explore(
        initial_state(), build_actions(), INVARIANTS,
        max_states=max_states, max_violations_per_invariant=1,
    )


if __name__ == "__main__":
    mode = "FIXED(수정 후 의미론)" if FIXED else "PRE-FIX(수정 전 의미론)"
    result = run()
    print(f"[M1 {mode}] states explored: {result.states_explored}"
          f"{' (TRUNCATED)' if result.frontier_truncated else ''}")
    if not result.violations:
        print(f"[M1 {mode}] no violations found")
    for v in result.violations:
        print()
        print(format_violation(v))
