"""설정 로더 — config/patterns.yaml · i18n.yaml (D17: 설정은 레포 내 YAML, 기동 시 1회 로드).

접근법:
- app/main.py 기동 시 load_config() 1회 → app.state.config 저장.
- 라우터에서는 request.app.state.config 또는 get_config()(모듈 캐시) 사용.
- 깨진 참조는 기동 실패로 조기 발견(docs/01 §3.4) — _validate가 RuntimeError.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# §3.3 dose_unit enum 7종 / D14 timing_food 4종 — 검증 기준
DOSE_UNITS = ("tablet", "capsule", "ml", "drop", "puff", "sachet", "application")
TIMING_FOOD = ("before_food", "after_food", "with_food", "empty_stomach")

_cache: dict[str, Any] | None = None


def _validate(cfg: dict[str, Any]) -> None:
    patterns = cfg["patterns"]
    order = cfg["pattern_order"]
    slot_order = cfg["slot_order"]
    if sorted(order) != sorted(patterns.keys()):
        raise RuntimeError("patterns.yaml: pattern_order와 patterns 키 불일치")
    if len(order) != 9:
        raise RuntimeError("patterns.yaml: 코어 8 + CUSTOM = 9항목이어야 함 (§3.4)")
    for key, p in patterns.items():
        for slot in p.get("slots") or []:
            if slot not in slot_order:
                raise RuntimeError(f"patterns.yaml: {key}의 slot {slot!r}이 slot_order 밖")
        if not (p.get("name") or {}).get("hi") or not (p.get("name") or {}).get("en"):
            raise RuntimeError(f"patterns.yaml: {key}에 hi/en name 결측")
    i18n = cfg["i18n"]
    for ns in ("slots", "timing_food", "dose_units", "prn_reasons", "days_of_week"):
        if ns not in i18n:
            raise RuntimeError(f"i18n.yaml: 네임스페이스 {ns!r} 결측")
    for unit in DOSE_UNITS:
        if unit not in i18n["dose_units"]:
            raise RuntimeError(f"i18n.yaml: dose_units.{unit} 결측 (enum 7종 — §3.3)")
    for tf in TIMING_FOOD:
        if tf not in i18n["timing_food"]:
            raise RuntimeError(f"i18n.yaml: timing_food.{tf} 결측 (D14)")


def load_config(config_dir: str | Path | None = None) -> dict[str, Any]:
    """반환 dict 구조(전 에이전트 공통 계약):
    {
      "patterns":       {키: {schedule_type, slots, digits, sort_order, is_active, name{hi,en}}},
      "pattern_order":  [9키 순서],
      "slot_order":     ["M","N","E","H"],
      "i18n":           {slots, timing_food, dose_units, prn_reasons, days_of_week,
                         duration_presets, ...(ui.* 추가 가능)},
    }
    """
    base = Path(config_dir) if config_dir else CONFIG_DIR
    with open(base / "patterns.yaml", encoding="utf-8") as f:
        pat = yaml.safe_load(f)
    with open(base / "i18n.yaml", encoding="utf-8") as f:
        i18n = yaml.safe_load(f)
    cfg = {
        "patterns": pat["patterns"],
        "pattern_order": pat["pattern_order"],
        "slot_order": pat["slot_order"],
        "i18n": i18n,
    }
    _validate(cfg)
    return cfg


def get_config() -> dict[str, Any]:
    """모듈 캐시 접근자 — 기동 후 어디서든 동일 dict."""
    global _cache
    if _cache is None:
        _cache = load_config()
    return _cache
