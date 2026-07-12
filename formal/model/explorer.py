"""의존성 없는 명시적 상태(explicit-state) 모델 체커.

TLA+/TLC 미보유 환경 폴백(docs 지시). BFS로 도달 가능 상태를 전수 열거하고
불변식 위반 시 초기 상태부터의 최소 길이 트레이스를 재구성한다.

설계 규약
- 상태는 해시 가능한 불변값(frozen dict 대용 중첩 튜플)으로 표현한다.
- Action = (name, guard, effect). effect는 새 상태를 반환하며 입력 상태를 변경하지 않는다.
- 비결정성(동시성·재시도·크래시·시계)은 전부 "동시에 enabled인 액션 집합"으로 환원한다.
- BFS이므로 최초 발견 위반 트레이스는 스텝 수 기준 최소다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


# ---------------------------------------------------------------------------
# 불변 상태 표현: dict <-> 정렬 튜플
# ---------------------------------------------------------------------------

def freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((k, freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    if isinstance(value, set):
        return tuple(sorted(freeze(v) for v in value))
    return value


def thaw(value: Any) -> Any:
    """freeze 역변환. 튜플-of-페어는 dict로 복원한다(모델 상태 규약)."""
    if isinstance(value, tuple):
        if all(isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], str) for v in value):
            return {k: thaw(v) for k, v in value}
        return [thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class Action:
    name: str
    guard: Callable[[dict], bool]
    effect: Callable[[dict], dict | Iterable[dict]]
    # effect가 상태 리스트를 반환하면 비결정 분기(예: 크래시 지점 선택)

    def enabled(self, state: dict) -> bool:
        return self.guard(state)


@dataclass
class Violation:
    invariant: str
    trace: list[str]              # 액션 이름 시퀀스 (최소 길이)
    state: dict                   # 위반 상태
    states_explored: int


@dataclass
class ExplorationResult:
    violations: list[Violation]
    states_explored: int
    frontier_truncated: bool      # 상태 한도 도달로 탐색이 불완전한지


def explore(
    initial: dict,
    actions: list[Action],
    invariants: dict[str, Callable[[dict], bool]],
    *,
    max_states: int = 2_000_000,
    stop_at_first: bool = False,
    max_violations_per_invariant: int = 1,
) -> ExplorationResult:
    init_f = freeze(initial)
    seen: set = {init_f}
    parent: dict = {init_f: (None, None)}   # frozen_state -> (frozen_parent, action_name)
    queue: deque = deque([init_f])
    violations: list[Violation] = []
    per_inv_count: dict[str, int] = {}
    truncated = False

    def build_trace(fstate) -> list[str]:
        names: list[str] = []
        cur = fstate
        while parent[cur][0] is not None:
            names.append(parent[cur][1])
            cur = parent[cur][0]
        return list(reversed(names))

    def check(fstate, state) -> bool:
        """위반 발견 시 True(stop_at_first면 탐색 중단 신호)."""
        for inv_name, pred in invariants.items():
            if per_inv_count.get(inv_name, 0) >= max_violations_per_invariant:
                continue
            try:
                ok = pred(state)
            except Exception as exc:  # 불변식 자체의 결함도 위반으로 승격
                ok = False
                inv_name = f"{inv_name}!predicate-error:{exc!r}"
            if not ok:
                base = inv_name.split("!", 1)[0]
                per_inv_count[base] = per_inv_count.get(base, 0) + 1
                violations.append(
                    Violation(inv_name, build_trace(fstate), state, len(seen))
                )
                if stop_at_first:
                    return True
        return False

    if check(init_f, thaw(init_f)):
        return ExplorationResult(violations, len(seen), False)

    while queue:
        if len(seen) >= max_states:
            truncated = True
            break
        cur_f = queue.popleft()
        cur = thaw(cur_f)
        for action in actions:
            if not action.enabled(cur):
                continue
            out = action.effect(dict(cur))
            successors = out if isinstance(out, list) else [out]
            for nxt in successors:
                nxt_f = freeze(nxt)
                if nxt_f in seen:
                    continue
                seen.add(nxt_f)
                parent[nxt_f] = (cur_f, action.name)
                if check(nxt_f, nxt):
                    return ExplorationResult(violations, len(seen), truncated)
                queue.append(nxt_f)

    return ExplorationResult(violations, len(seen), truncated)


def format_violation(v: Violation) -> str:
    lines = [f"불변식 위반: {v.invariant}", f"탐색 상태 수: {v.states_explored}", "최소 트레이스:"]
    for i, step in enumerate(v.trace, 1):
        lines.append(f"  {i:2d}. {step}")
    lines.append("위반 상태(요약):")
    for k in sorted(v.state):
        lines.append(f"  {k} = {v.state[k]!r}")
    return "\n".join(lines)
