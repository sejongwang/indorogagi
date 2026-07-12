# formal/ — indoro 안전 명세·모델 검사

의약품 안내 프로토타입의 상태기계 안전성 검증 산출물. TLA+/TLC 미보유 환경이라 docs 지침대로 **의존성 없는 Python 명시적 상태 탐색기**를 사용한다(표준 라이브러리만, Python 3.11+).

| 파일 | 내용 |
|---|---|
| [spec.md](spec.md) | 구현 독립 명세 — 6개 생명주기 전이표 + 10개 교차 불변식 형식화 (docs/01·08·09만 근거) |
| [model/explorer.py](model/explorer.py) | 범용 BFS 탐색기 — 도달 상태 전수 열거, 위반 시 최소 트레이스 재구성 |
| [model/m1_prescription.py](model/m1_prescription.py) | M1·M2: 발급·멱등 재시도·수정·재발급·열람·시간 경과 (구현 의미론) |
| [model/m2_governance.py](model/m2_governance.py) | M3–M5: 검토·임포트 갱신·retirement batch (구현 의미론, `--buggy`로 수정 전 재현) |
| [counterexamples.md](counterexamples.md) | 랭킹된 반례 4건(전부 수정 완료) + 문서-코드 편차 3건 |
| [traceability.md](traceability.md) | 불변식 → 집행 코드 라인 → 모델 → 테스트 매트릭스 |
| [verdict.md](verdict.md) | 최종 판정 + 코드로 증명 불가한 잔여 리스크 |

실행:

```bash
python3 formal/model/m1_prescription.py     # ~10분, 수정 전 의미론의 반례 3건 재검출
python3 formal/model/m2_governance.py       # ~1.5분, 현재 코드 의미론 — 위반 0
python3 formal/model/m2_governance.py --buggy   # 수정 전 의미론 — INV-8 2스텝 반례
```

모델은 **코드가 실제로 하는 일**(무가드 UPDATE, replay 비교 부재 등 수정 전 의미론 포함)을 인코딩한다. 명세와의 차이가 곧 반례이며, 각 반례는 실서버 회귀 테스트(server/tests/test_lifecycle_invariants.py, test_catalog_governance.py)로 고정했다. 코드 전이 의미론이 바뀌면 모델의 해당 전이 함수도 함께 갱신할 것.
