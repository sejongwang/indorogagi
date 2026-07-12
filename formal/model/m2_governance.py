"""M3–M5 — 카탈로그 검토·운영 생명주기·retirement 상호작용 모델.

구현 의미론 출처(직접 판독): server/app/catalog_governance.py
  transition_presentation(207) / record_source_refresh(324) / record_source_ingest_issue(384)
  decide_retirement_candidate(845) / _change_batch_state(1010) / apply_retirement_batch(1132)
서버는 모든 전이를 BEGIN IMMEDIATE 단일 트랜잭션 + 버전 조건부 UPDATE(rowcount 검사)로
수행하므로, 각 전이를 원자 액션으로 모델링한다. 버전 가드가 막는 stale 액션은
상태를 바꾸지 않으므로(no-op) 열거에서 제외 — 가드 존재는 코드 판독으로 확인됨.

BUGGY_REJECT_RESET=True 는 수정 전 record_source_refresh 의미론(projection 변경 시
rejected→needs_review)을 재현한다 — CX-2의 기계 검증용.

불변식:
- INV-8': 사람이 차단(rejected)한 presentation은 사람의 review_requested 없이
  검색 가능 상태가 되지 않는다. (searchable = lifecycle==active ∧ review≠rejected)
- INV-4': retired는 오직 approved batch의 apply로만 발생하고, apply 시점 버전이
  후보 생성 시점 버전과 일치한다(증거 사슬 무결).
- LIFE: retired에서 나가는 전이 없음(일반 UI 미지원 — 09 §9).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from explorer import Action, explore, format_violation  # noqa: E402

BUGGY_REJECT_RESET = "--buggy" in sys.argv

N_PRES = 2
MAX_VERSION = 7
MAX_BATCHES = 2
MAX_IMPORTS = 2   # presentation당 임포트 사건 수 경계

REVIEWS = ("unverified", "needs_review", "approved", "rejected")
OPEN_BATCH = ("proposed", "under_review", "approved")


def initial_state() -> dict:
    return {
        "pres": [
            {"review": "needs_review", "lifecycle": "active", "version": 1,
             "human_blocked": False, "imports": 0,
             "retired_via_apply": False, "retire_evidence_ok": True}
            for _ in range(N_PRES)
        ],
        "batches": [],   # {status, version, cands: [{p, decision, cand_v, expected_pv, created_pv}]}
    }


def searchable(p: dict) -> bool:
    """drug_catalog.py:1561-1562 검색 필터와 동일."""
    return p["lifecycle"] == "active" and p["review"] != "rejected"


def _copy(s: dict) -> dict:
    return {
        "pres": [dict(p) for p in s["pres"]],
        "batches": [
            {"status": b["status"], "version": b["version"],
             "cands": [dict(c) for c in b["cands"]]}
            for b in s["batches"]
        ],
    }


# ------------------------------------------------------------- 사람 검토 (M4)

def act_review(s: dict, i: int, action: str) -> dict:
    s = _copy(s)
    p = s["pres"][i]
    if action == "review_requested":
        p["review"] = "needs_review"
        p["human_blocked"] = False        # 사람이 의식적으로 재개 — 차단 해제 권한은 사람뿐
    elif action == "review_approved":
        p["review"] = "approved"
    elif action == "review_rejected":
        p["review"] = "rejected"
        p["human_blocked"] = True
    elif action == "lifecycle_inactivated":
        p["lifecycle"] = "inactive"
    elif action == "lifecycle_reactivated":
        p["lifecycle"] = "active"
    p["version"] += 1
    return s


# ------------------------------------------------------------- 임포터 (M3 접점)

def act_import(s: dict, i: int, kind: str) -> dict:
    """record_source_refresh / record_source_ingest_issue 의미론."""
    s = _copy(s)
    p = s["pres"][i]
    p["imports"] += 1
    if kind == "changed":
        if p["review"] == "approved":
            p["review"] = "needs_review"
        elif p["review"] == "rejected" and BUGGY_REJECT_RESET:
            p["review"] = "needs_review"   # 수정 전 의미론 — human_blocked는 그대로
    elif kind == "quarantine":
        if p["review"] != "rejected":
            p["review"] = "needs_review"
    # kind == "same": 검토 상태 불변
    p["version"] += 1                       # 어느 경우든 evidence 전진 → 후보 stale화
    return s


# ------------------------------------------------------------- retirement (M5)

def act_propose(s: dict, i: int) -> dict:
    s = _copy(s)
    s["batches"].append({
        "status": "proposed", "version": 1,
        "cands": [{
            "p": i, "decision": "pending", "cand_v": 1,
            "expected_pv": s["pres"][i]["version"],
            "created_pv": s["pres"][i]["version"],
        }],
    })
    return s


def can_propose(s: dict, i: int) -> bool:
    if len(s["batches"]) >= MAX_BATCHES or s["pres"][i]["lifecycle"] == "retired":
        return False
    for b in s["batches"]:
        if b["status"] in OPEN_BATCH and any(c["p"] == i for c in b["cands"]):
            return False                    # 열린 batch 중복 제안 금지 (docs 09 §7)
    return True


def act_decide(s: dict, bi: int, decision: str) -> dict:
    s = _copy(s)
    b = s["batches"][bi]
    c = b["cands"][0]
    c["decision"] = decision
    c["cand_v"] += 1
    c["expected_pv"] = s["pres"][c["p"]]["version"]   # 가드 통과 시 visible==current==창조시점
    b["status"] = "under_review"
    b["version"] += 1
    return s


def can_decide(s: dict, bi: int) -> bool:
    if bi >= len(s["batches"]):
        return False
    b = s["batches"][bi]
    if b["status"] not in ("proposed", "under_review"):
        return False
    c = b["cands"][0]
    # catalog_governance.py:895 — 후보 생성 시점 버전 == 현재 presentation 버전일 때만
    return c["expected_pv"] == s["pres"][c["p"]]["version"]


def act_approve(s: dict, bi: int) -> dict:
    s = _copy(s)
    s["batches"][bi]["status"] = "approved"
    s["batches"][bi]["version"] += 1
    return s


def can_approve(s: dict, bi: int) -> bool:
    if bi >= len(s["batches"]):
        return False
    b = s["batches"][bi]
    if b["status"] not in ("proposed", "under_review"):
        return False
    for c in b["cands"]:
        if c["decision"] not in ("keep_active", "retire"):
            return False
        if s["pres"][c["p"]]["version"] != c["expected_pv"]:   # :1039-1054 stale 차단
            return False
    return True


def act_apply(s: dict, bi: int) -> dict:
    s = _copy(s)
    b = s["batches"][bi]
    for c in b["cands"]:
        if c["decision"] == "retire":
            p = s["pres"][c["p"]]
            p["lifecycle"] = "retired"
            p["retired_via_apply"] = True
            # 증거 사슬: apply 직전 버전 == 후보 생성 시점 버전이어야 무결
            p["retire_evidence_ok"] = (
                p["version"] == c["expected_pv"] == c["created_pv"]
            )
            p["version"] += 1
    b["status"] = "applied"
    b["version"] += 1
    return s


def can_apply(s: dict, bi: int) -> bool:
    if bi >= len(s["batches"]):
        return False
    b = s["batches"][bi]
    if b["status"] != "approved":
        return False
    for c in b["cands"]:
        if c["decision"] not in ("keep_active", "retire"):
            return False
        if s["pres"][c["p"]]["version"] != c["expected_pv"]:   # :1172-1179 재확인
            return False
        if c["decision"] == "retire" and s["pres"][c["p"]]["lifecycle"] == "retired":
            return False                                       # :1184 가드
    return True


def act_cancel(s: dict, bi: int) -> dict:
    s = _copy(s)
    s["batches"][bi]["status"] = "cancelled"
    s["batches"][bi]["version"] += 1
    return s


# ------------------------------------------------------------- 액션 목록

def build_actions() -> list[Action]:
    acts: list[Action] = []
    for i in range(N_PRES):
        p_lt_max = lambda s, i=i: s["pres"][i]["version"] < MAX_VERSION

        for action, guard in [
            ("review_requested", lambda s, i=i: s["pres"][i]["review"] in ("unverified", "rejected", "approved")),
            ("review_approved", lambda s, i=i: s["pres"][i]["review"] == "needs_review"),
            ("review_rejected", lambda s, i=i: s["pres"][i]["review"] == "needs_review"),
            ("lifecycle_inactivated", lambda s, i=i: s["pres"][i]["lifecycle"] == "active"),
            ("lifecycle_reactivated", lambda s, i=i: s["pres"][i]["lifecycle"] == "inactive"),
        ]:
            acts.append(Action(
                f"{action}(p{i})",
                lambda s, g=guard, i=i: g(s) and s["pres"][i]["version"] < MAX_VERSION,
                lambda s, i=i, a=action: act_review(s, i, a),
            ))
        for kind in ("same", "changed", "quarantine"):
            acts.append(Action(
                f"import_{kind}(p{i})",
                lambda s, i=i: s["pres"][i]["imports"] < MAX_IMPORTS
                and s["pres"][i]["version"] < MAX_VERSION,
                lambda s, i=i, k=kind: act_import(s, i, k),
            ))
        acts.append(Action(
            f"propose(p{i})",
            lambda s, i=i: can_propose(s, i),
            lambda s, i=i: act_propose(s, i),
        ))
    for bi in range(MAX_BATCHES):
        for decision in ("keep_active", "retire", "needs_investigation"):
            acts.append(Action(
                f"decide(b{bi},{decision})",
                lambda s, bi=bi: can_decide(s, bi),
                lambda s, bi=bi, d=decision: act_decide(s, bi, d),
            ))
        acts.append(Action(f"approve(b{bi})", lambda s, bi=bi: can_approve(s, bi),
                           lambda s, bi=bi: act_approve(s, bi)))
        acts.append(Action(f"apply(b{bi})", lambda s, bi=bi: can_apply(s, bi),
                           lambda s, bi=bi: act_apply(s, bi)))
        acts.append(Action(
            f"cancel(b{bi})",
            lambda s, bi=bi: bi < len(s["batches"]) and s["batches"][bi]["status"] in OPEN_BATCH,
            lambda s, bi=bi: act_cancel(s, bi),
        ))
    return acts


# ------------------------------------------------------------- 불변식

def inv8_human_block(s: dict) -> bool:
    """사람이 차단한 레코드는 사람의 재개 없이 검색에 노출되지 않는다."""
    return all(not (p["human_blocked"] and searchable(p)) for p in s["pres"])


def inv4_retire_only_via_apply(s: dict) -> bool:
    return all(p["retired_via_apply"] for p in s["pres"] if p["lifecycle"] == "retired")


def inv4_evidence_chain(s: dict) -> bool:
    """retire는 후보 생성 시점 증거 버전 그대로의 presentation에만 적용된다."""
    return all(p["retire_evidence_ok"] for p in s["pres"])


def life_retired_terminal(s: dict) -> bool:
    """retired가 된 presentation은 다시 active/inactive가 되지 않는다."""
    return all(
        p["lifecycle"] == "retired" or not p["retired_via_apply"] for p in s["pres"]
    )


INVARIANTS = {
    "INV-8:human-block-not-searchable": inv8_human_block,
    "INV-4:retire-only-via-approved-apply": inv4_retire_only_via_apply,
    "INV-4:evidence-version-chain": inv4_evidence_chain,
    "LIFE:retired-terminal": life_retired_terminal,
}


if __name__ == "__main__":
    mode = "BUGGY(수정 전 의미론)" if BUGGY_REJECT_RESET else "FIXED(현재 코드 의미론)"
    result = explore(initial_state(), build_actions(), INVARIANTS,
                     max_states=1_200_000, max_violations_per_invariant=1)
    print(f"[M2 {mode}] states explored: {result.states_explored}"
          f"{' (TRUNCATED)' if result.frontier_truncated else ''}")
    if not result.violations:
        print(f"[M2 {mode}] no violations found")
    for v in result.violations:
        print()
        print(format_violation(v))
