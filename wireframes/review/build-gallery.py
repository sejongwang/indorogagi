#!/usr/bin/env python3
"""indoro B1 와이어프레임 라이브 갤러리 빌더 — 화면 소스를 인라인·패치해 아티팩트 HTML 생성"""
import json, pathlib

WF = pathlib.Path('/Users/junehwi/indoro/wireframes')
OUT = pathlib.Path(__file__).parent / 'gallery-preview.html'  # 생성물 — .gitignore 대상

css = (WF / 'shared/wf.css').read_text()
wfjs = (WF / 'shared/wf.js').read_text()
fixtures = (WF / 'shared/fixtures.js').read_text()


def patch_runtime(src: str) -> str:
    # srcdoc(about:srcdoc)에는 쿼리스트링이 없으므로 주입 변수로 대체.
    # 'window.location.search'를 먼저 치환해야 잔여 'window.' 접두사로 문법이 깨지지 않는다.
    src = src.replace('window.location.search', '(window.__WF_QS||"")')
    src = src.replace('location.search', '(window.__WF_QS||"")')
    # about:srcdoc에서 replaceState/assign은 실패하거나 프레임을 깨뜨림 — 갤러리(__WF_QS 존재)에서만 무해화
    src = src.replace(
        'window.history.replaceState(null, "", buildURL(this.params));',
        'try { window.history.replaceState(null, "", buildURL(this.params)); } catch (e) {}')
    src = src.replace(
        'window.location.assign(buildURL(params));',
        'if (!window.__WF_QS) window.location.assign(buildURL(params));')
    return src


def inline_shared(src: str) -> str:
    src = src.replace('<link rel="stylesheet" href="../shared/wf.css">', '<style>\n' + css + '\n</style>')
    src = src.replace('<script src="../shared/fixtures.js"></script>', '<script>\n' + fixtures + '\n</script>')
    src = src.replace('<script src="../shared/wf.js"></script>', '<script>\n' + patch_runtime(wfjs) + '\n</script>')
    return src


p1 = patch_runtime(inline_shared((WF / 'pharmacist/p1-input.html').read_text()))
p3 = patch_runtime(inline_shared((WF / 'pharmacist/p3-qr.html').read_text()))
s1 = patch_runtime((WF / 'patient/s1-landing.html').read_text())


def enc(s: str) -> str:
    return json.dumps(s, ensure_ascii=False).replace('</', '<\\/')


FRAMES = [
    ('p1', '?tb=0', 'P1 · 기본', '3항목 입력 중 — 접힘 요약·최근 약 레일·패턴 칩'),
    ('p1', '?state=confirm-sheet&tb=0', 'P1 · 확인 시트(P2)', '발급 전 대조 — ml 총량 경고 존·전폭 발급 버튼'),
    ('p1', '?state=offline&tb=0', 'P1 · 오프라인', '배너·Outbox 배지·자동완성 비활성 — 발급은 즉시'),
    ('p3', '?tb=0', 'P3 · 화면 제시', 'QR 첫 화면 중앙 + 백업 코드 — 밝기 안내'),
    ('p3', '?state=print&tb=0', 'P3 · 인쇄(기본 경로)', '약봉투 부착용 미리보기 — ECC Q · ≥2cm'),
    ('p3', '?state=offline&tb=0', 'P3 · 오프라인 발급', 'QR만·코드 없음·동기화 대기 배지(§2.3)'),
    ('s1', '?tb=0', 'S1 · 힌디(기본)', '신뢰 헤더→봉투 대조→하루 시간표가 첫 화면 완결'),
    ('s1', '?lang=en&tb=0', 'S1 · English', '언어 토글 시 아이콘·그리드 골격 이동 0'),
    ('s1', '?fx=b&tb=0', 'S1 · 전 패턴(FX-B)', '10항목 — QID·Weekly·STAT·PRN·CUSTOM 변형 카드'),
    ('s1', '?fx=c&tb=0', 'S1 · 엣지(FX-C)', '수정됨 배지·곧 만료 D-3·½정·시드 밖 약명'),
]

sections = {
    'p1': ('약사 — P1 처방 입력', '수기 처방전을 항목당 20~40초에 구조화 입력. 숫자 키보드 0회, 탭만으로.', ['p1']),
    'p3': ('약사 — P3 QR 제시', '발급 직후 환자에게 건네는 접점. 인쇄가 기본, 화면 제시는 폴백.', ['p3']),
    's1': ('환자 — S1 복약 안내', '앱 설치 없이 QR 스캔 한 번으로. 아이콘+숫자+음성 — 텍스트 독해 없이 성립.', ['s1']),
}

cards = {k: [] for k in sections}
for i, (src, qs, label, cap) in enumerate(FRAMES):
    cards[src].append(
        f'<figure class="frame"><figcaption><span class="st">{label}</span>'
        f'<code>{qs.replace("&tb=0", "").replace("?tb=0", "기본") or "기본"}</code></figcaption>'
        f'<div class="bezel"><iframe id="f{i}" data-src="{src}" data-qs="{qs}" '
        f'title="{label}"></iframe></div>'
        f'<p class="cap">{cap}</p></figure>')

section_html = ''
flow_no = {'p1': '1', 'p3': '2', 's1': '3'}
for key, (title, sub, _) in sections.items():
    section_html += (
        f'<section><header class="sec"><span class="no">{flow_no[key]}</span>'
        f'<div><h2>{title}</h2><p>{sub}</p></div></header>'
        f'<div class="rail">{"".join(cards[key])}</div></section>')

html = """<meta charset="utf-8">
<title>indoro B1 와이어프레임</title>
<style>
  :root {
    --bg: #f1f1f1; --card: #ffffff; --ink: #171717; --sub: #737373;
    --line: #ebebeb; --accent: #262626;
    --sans: Figtree, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans KR", sans-serif;
    --mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, monospace;
  }
  body { background: var(--bg); color: var(--ink); font-family: var(--sans); margin: 0; }
  .wrap { max-width: 1280px; margin: 0 auto; padding: 40px 24px 72px; }
  .masthead { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 16px 24px; padding-bottom: 20px; border-bottom: 1px solid var(--line); }
  .masthead h1 { font-size: 26px; font-weight: 600; letter-spacing: -0.02em; margin: 0 0 6px; text-wrap: balance; }
  .masthead .tag { font-size: 14px; color: var(--sub); margin: 0; max-width: 56ch; line-height: 1.6; }
  .flow { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; }
  .flow b { background: var(--accent); color: #fff; border-radius: 999px; padding: 8px 14px; font-weight: 600; white-space: nowrap; }
  .flow span { color: var(--sub); }
  section { margin-top: 44px; }
  .sec { display: flex; gap: 14px; align-items: baseline; margin-bottom: 16px; }
  .sec .no { font-family: var(--mono); font-size: 13px; color: var(--sub); border: 1px solid var(--line); background: var(--card); border-radius: 999px; width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center; flex: none; transform: translateY(-2px); }
  .sec h2 { font-size: 18px; font-weight: 600; margin: 0 0 4px; }
  .sec p { font-size: 14px; color: var(--sub); margin: 0; line-height: 1.6; }
  .rail { display: flex; gap: 20px; overflow-x: auto; padding: 4px 4px 16px; scroll-snap-type: x proximity; }
  .frame { flex: none; width: 372px; margin: 0; scroll-snap-align: start; }
  .frame figcaption { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
  .frame .st { font-size: 14px; font-weight: 600; }
  .frame code { font-family: var(--mono); font-size: 11px; color: var(--sub); background: var(--card); border: 1px solid var(--line); border-radius: 6px; padding: 3px 7px; white-space: nowrap; }
  .bezel { background: var(--card); border: 1px solid #dcdcdc; border-radius: 22px; padding: 5px; box-shadow: 0 1px 2px rgba(23,23,23,.06), 0 8px 24px rgba(23,23,23,.05); }
  .bezel iframe { display: block; width: 360px; height: 700px; border: 0; border-radius: 18px; background: #fff; }
  .cap { font-size: 13px; color: var(--sub); line-height: 1.6; margin: 10px 2px 0; }
  .note { margin-top: 52px; background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 20px 22px; display: grid; gap: 8px; }
  .note h2 { font-size: 15px; font-weight: 600; margin: 0 0 2px; }
  .note p { font-size: 13.5px; color: var(--sub); margin: 0; line-height: 1.7; }
  .note code { font-family: var(--mono); font-size: 12px; color: var(--ink); background: var(--bg); border-radius: 5px; padding: 2px 6px; }
  @media (max-width: 460px) { .wrap { padding: 28px 14px 56px; } .frame { width: 92vw; } .bezel iframe { width: 100%; } }
</style>
<div class="wrap">
  <header class="masthead">
    <div>
      <h1>indoro — B1 관통 코어 와이어프레임</h1>
      <p class="tag">프레임 안은 이미지가 아니라 실행 중인 실제 화면 HTML입니다 — 스크롤·탭·칩 선택이 그대로 동작해요. astryx neutral 토큰, 360px 기준.</p>
    </div>
    <div class="flow" aria-label="관통 데모 순서"><b>P1 입력</b><span>→</span><b>P3 QR</b><span>→</span><b>S1 환자</b></div>
  </header>
  __SECTIONS__
  <div class="note">
    <h2>팀 리뷰 안내</h2>
    <p>클릭 워크스루 정본은 로컬 <code>wireframes/index.html</code> (<code>python3 -m http.server 8493 --directory wireframes</code>) — 폰 실기기 1대 필수, 라운드 상한 2회.</p>
    <p>상태 전환은 URL 파라미터: <code>?state=</code> <code>?lang=hi|en</code> <code>?fx=a|b|c</code> <code>?color=0</code> <code>?tb=0</code>(툴바 접기). 리뷰 주석은 <code>wireframes/review/batch1-notes.md</code>에 <code>- [ ] 화면ID/요소: 문제 — 수정 지시</code> 형식으로.</p>
    <p>이 갤러리 프레임에서는 페이지 이동(발급→P3, 언어 링크)만 막혀 있습니다 — 상태별 화면은 옆 프레임으로 제공.</p>
  </div>
</div>
<script>
var SRC = { p1: __P1__, p3: __P3__, s1: __S1__ };
document.querySelectorAll('iframe[data-src]').forEach(function (f) {
  var qs = f.getAttribute('data-qs');
  var head = '<script>window.__WF_QS=' + JSON.stringify(qs) +
    ';document.addEventListener("click",function(e){var a=e.target&&e.target.closest&&e.target.closest("a");' +
    'if(a){var h=a.getAttribute("href")||"";if(h.charAt(0)==="?"||h.indexOf("http")===0||h.indexOf(".html")>-1){e.preventDefault();}}},true);<\\/script>';
  var doc = SRC[f.getAttribute('data-src')];
  f.srcdoc = doc.replace(/<head([^>]*)>/i, '<head$1>' + head);
});
</script>
"""
html = html.replace('__SECTIONS__', section_html).replace('__P1__', enc(p1)).replace('__P3__', enc(p3)).replace('__S1__', enc(s1))
OUT.write_text(html)
print(f'생성: {OUT} ({OUT.stat().st_size / 1024:.0f}KB)')
