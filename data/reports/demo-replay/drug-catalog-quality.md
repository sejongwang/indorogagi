# Drug Catalog Quality Report

- Generated: `2026-07-10T10:30:42Z`
- Database mode: `demo`
- Idempotent replay: `yes`

- Mode: `apply`
- Sources: 2

## Combined quality

| Metric | Count |
| --- | ---: |
| raw total | 27 |
| imported | 27 |
| excluded | 0 |
| quarantined | 0 |
| needs review | 1 |
| duplicate candidates | 0 |
| missing brand name | 0 |
| missing generic name | 1 |
| missing strength | 1 |
| missing dosage form | 1 |
| missing manufacturer | 27 |
| missing source | 0 |
| unknown strength unit | 0 |
| unknown dosage form | 0 |

## Source: `nppa-antidiabetes-formulations-2026-03`

- Version: `2026-03`
- Usage scope: `production`
- Mode: `apply`
- Source accessed: `2026-07-10`
- Source updated: `2026-05-06`
- Source input: `https://www.nppa.gov.in/storage/uploads/pdf/2845-fb-posts-anti-diabetes-a2491c25ce7e20c9200884e1f92cf68a.pdf`
- Reuse status: `approved`
- Licence: `NPPA Copyright Policy` (https://www.nppa.gov.in/en/copyrightpolicy)
- Source artifact manifest: `data/catalog/nppa-anti-diabetes-2026-03.source-manifest.json`
- Source artifact SHA-256: `fd23814ae3a0009a0d8d6e14d079ff3e72605edfae6041e3529ccd988f311e62`
- Transformation: `Human-reviewed PDF table transcription to committed CSV, then deterministic CSV-to-package conversion.`
- Input SHA-256: `b4413c5423a9c31b4062ffa3572e2fdf606118a2f21e1e5ae048d65c4f25456a`
- Records SHA-256: `e06801716c56985d4a3d57d4c9c188b85aee95dbbde518861521dc23668a5ad7`
- Approval registry SHA-256: `4d304b6ef995ac6b4770497b2667025a038ebc3dd6d9bf3c2b16f20bb7fe453e`

| Metric | Count |
| --- | ---: |
| raw total | 11 |
| imported | 11 |
| excluded | 0 |
| quarantined | 0 |
| needs review | 0 |
| duplicate candidates | 0 |
| missing brand name | 0 |
| missing generic name | 0 |
| missing strength | 0 |
| missing dosage form | 0 |
| missing manufacturer | 11 |
| missing source | 0 |
| unknown strength unit | 0 |
| unknown dosage form | 0 |

## Source: `indoro-synthetic-demo-v1`

- Version: `1.0.0`
- Usage scope: `demo`
- Mode: `apply`
- Source accessed: ``
- Source updated: `2026-07-10`
- Source input: `data/catalog/indoro-synthetic-demo-v1.json`
- Reuse status: `demo_only`
- Licence: `Project-authored synthetic fixture` (not applicable)
- Source artifact manifest: `not applicable`
- Source artifact SHA-256: `not applicable`
- Transformation: `not applicable`
- Input SHA-256: `3d7c85f5f0b6fa7c3296d5d659f1ccd87a4a46f9e9161d7cc7550f552403c0a7`
- Records SHA-256: `e52a02488273d73eadfbf57febb70a53e832b7bb662b575194fceb6077809238`
- Approval registry SHA-256: `not applicable`

| Metric | Count |
| --- | ---: |
| raw total | 16 |
| imported | 16 |
| excluded | 0 |
| quarantined | 0 |
| needs review | 1 |
| duplicate candidates | 0 |
| missing brand name | 0 |
| missing generic name | 1 |
| missing strength | 1 |
| missing dosage form | 1 |
| missing manufacturer | 16 |
| missing source | 0 |
| unknown strength unit | 0 |
| unknown dosage form | 0 |

## Current catalog review queues

These are candidates for human review. No records are merged automatically.

- Same brand, different ingredient: 1
- Same composition, multiple brands: 3
- Dangerous similar names: 4

### Source breakdown

| Source | Tier | Scope | Presentations |
| --- | ---: | --- | ---: |
| indoro-synthetic-demo-v1 | 3 | demo | 16 |
| nppa-antidiabetes-formulations-2026-03 | 1 | production | 11 |
