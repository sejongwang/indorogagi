/* ============================================================
   indoro 와이어프레임 공용 러너 (wf.js)
   - URL 파라미터 파서: ?state= / ?lang=hi|en / ?fx=a|b|c / ?color=0|1
   - 개발 툴바 렌더러(빗금 배경, "제품 아님") — 화면이 등록한 상태 목록을 버튼으로
   - <html> 속성 토글: lang + data-lang / data-state / data-fx / data-color
   - 계측 마커: 콘솔 데모 로그만 (WF.mark / WF.beacon, [data-beacon] 자동 바인딩)
   - 기본 동작: state/lang/color는 무리로드 인플레이스 전환, fx는 리로드(데이터 재렌더 보장)
   ============================================================ */
(function (window, document) {
  "use strict";

  var LANGS = ["hi", "en"];

  function parseQS() {
    var out = {};
    var q = window.location.search.replace(/^\?/, "");
    if (!q) return out;
    q.split("&").forEach(function (pair) {
      if (!pair) return;
      var i = pair.indexOf("=");
      var k = decodeURIComponent(i < 0 ? pair : pair.slice(0, i));
      var v = i < 0 ? "" : decodeURIComponent(pair.slice(i + 1));
      out[k] = v;
    });
    return out;
  }

  /* 툴바 innerHTML에 들어가는 모든 문자열은 이스케이프(방어적 — 값 자체는
     화면이 등록한 화이트리스트·검증된 파라미터만 도달) */
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function buildURL(params) {
    var keys = Object.keys(params).filter(function (k) {
      return params[k] !== null && params[k] !== undefined && params[k] !== "";
    });
    var qs = keys.map(function (k) {
      return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]);
    }).join("&");
    return window.location.pathname + (qs ? "?" + qs : "") + window.location.hash;
  }

  var WF = {
    ctx: null,
    _opts: null,
    params: parseQS(),

    /* ----- 초기화: 각 화면이 <body> 끝에서 1회 호출 ----- */
    init: function (opts) {
      opts = opts || {};
      var o = {
        screen: opts.screen || "??",                 // 화면 ID (예: "P1")
        states: opts.states || ["default"],          // 등록 상태 목록 — 인벤토리 "상태 변형" 전부
        defaultState: opts.defaultState || (opts.states ? opts.states[0] : "default"),
        langs: opts.langs || null,                   // ["hi","en"] 전달 시 언어 토글 노출. null = 병기 화면
        defaultLang: opts.defaultLang || "hi",
        fxList: opts.fxList || null,                 // ["a","b","c"] 전달 시 FX 스위처 노출
        defaultFx: opts.defaultFx || "a",
        showColor: opts.showColor !== false,         // 배지 없는 화면은 false로 숨김
        reloadOn: opts.reloadOn || ["fx"],           // 이 키 변경 시 전체 리로드(재렌더 보장)
        onChange: opts.onChange || null              // function(ctx, changedKey)
      };
      this._opts = o;

      var qs = this.params;
      var ctx = {
        screen: o.screen,
        state: (o.states.indexOf(qs.state) >= 0) ? qs.state : o.defaultState,
        lang: o.langs ? ((o.langs.indexOf(qs.lang) >= 0) ? qs.lang : o.defaultLang) : null,
        fx: o.fxList ? ((o.fxList.indexOf(qs.fx) >= 0) ? qs.fx : o.defaultFx) : null,
        color: (qs.color === "0") ? "0" : "1"
      };
      this.ctx = ctx;

      this._apply();
      this._renderToolbar();
      this._bindBeacons();

      console.info("[WF] " + o.screen + " · state=" + ctx.state +
        (ctx.lang ? " · lang=" + ctx.lang : "") +
        (ctx.fx ? " · fx=" + ctx.fx : "") +
        " · color=" + ctx.color + " (개발 툴바로 전환 가능)");
      return ctx;
    },

    /* ----- 전환 (툴바 내부/화면 코드 공용) ----- */
    set: function (key, value) {
      var ctx = this.ctx, o = this._opts;
      if (!ctx || ctx[key] === value) return;
      if (o.reloadOn.indexOf(key) >= 0) {
        var params = {};
        for (var k in this.params) params[k] = this.params[k];
        params[key] = value;
        window.location.assign(buildURL(params));
        return;
      }
      ctx[key] = value;
      this._apply();
      this._syncURL(key, value);
      this._renderToolbar();
      if (typeof o.onChange === "function") o.onChange(ctx, key);
    },

    /* ----- 현재 FX 픽스처 (window.INDORO_FX 필요) ----- */
    fixture: function () {
      if (!this.ctx || !this.ctx.fx || !window.INDORO_FX) return null;
      return window.INDORO_FX[this.ctx.fx] || null;
    },

    /* ----- 계측 데모 로그 (실전송 없음) ----- */
    mark: function (label, props) {   // 서버 권위 계측 자리: WF.mark("T0 client_input_id 생성")
      console.info("%c[계측 데모] " + label, "color:#6e3500;font-weight:bold", props || "");
    },
    beacon: function (type, props) {  // 클라 beacon 자리: WF.beacon("media.video_play", {...})
      console.info("%c[beacon 데모] " + type, "color:#00458c;font-weight:bold", props || "");
    },

    /* ================= 내부 ================= */
    _apply: function () {
      var ctx = this.ctx;
      var html = document.documentElement;
      html.setAttribute("data-state", ctx.state);
      html.setAttribute("data-color", ctx.color);
      if (ctx.fx) html.setAttribute("data-fx", ctx.fx);
      if (ctx.lang) {                       // 언어 토글 화면만 — 미세팅이면 hi/en 병기 유지
        html.setAttribute("lang", ctx.lang);
        html.setAttribute("data-lang", ctx.lang);
      }
      this._applyShowHide();
    },

    /* data-wf-show="stateA stateB" → 해당 상태에서만 표시.
       data-wf-hide="stateA"       → 해당 상태에서 숨김. */
    _applyShowHide: function () {
      var state = this.ctx.state;
      var nodes = document.querySelectorAll("[data-wf-show], [data-wf-hide]");
      for (var i = 0; i < nodes.length; i++) {
        var el = nodes[i];
        var show = el.getAttribute("data-wf-show");
        var hide = el.getAttribute("data-wf-hide");
        var visible = true;
        if (show !== null) visible = (" " + show + " ").indexOf(" " + state + " ") >= 0;
        if (visible && hide !== null) visible = (" " + hide + " ").indexOf(" " + state + " ") < 0;
        el.classList.toggle("wf-hidden", !visible);
      }
    },

    _syncURL: function (key, value) {
      this.params[key] = value;
      if (window.history && window.history.replaceState) {
        window.history.replaceState(null, "", buildURL(this.params));
      }
    },

    _renderToolbar: function () {
      var self = this, o = this._opts, ctx = this.ctx;
      var tb = document.querySelector(".wf-toolbar");
      if (!tb) {
        tb = document.createElement("aside");
        tb.className = "wf-toolbar";
        tb.setAttribute("aria-label", "개발 툴바 (제품 아님)");
        document.body.appendChild(tb);
      }
      var collapsed = window.sessionStorage && window.sessionStorage.getItem("wfTbCollapsed") === "1";
      tb.classList.toggle("is-collapsed", !!collapsed);

      var htmlStr = "";
      htmlStr += '<div class="wf-tb-head">WF · ' + esc(o.screen) +
        ' <span class="wf-tb-note">제품 아님</span>' +
        ' <button type="button" class="wf-tb-toggle" data-wf-tb="toggle">' + (collapsed ? "+" : "−") + "</button></div>";
      htmlStr += '<div class="wf-tb-body">';
      htmlStr += this._group("상태", "state", o.states, ctx.state);
      if (o.langs) htmlStr += this._group("언어", "lang", o.langs, ctx.lang);
      if (o.fxList) htmlStr += this._group("FX", "fx", o.fxList.map(function (f) { return f.toUpperCase(); }), ctx.fx.toUpperCase());
      if (o.showColor) htmlStr += this._group("색", "color", ["1", "0"], ctx.color, { "1": "컬러", "0": "회색" });
      htmlStr += "</div>";
      tb.innerHTML = htmlStr;

      tb.onclick = function (e) {
        var btn = e.target.closest("[data-wf-tb]");
        if (!btn) return;
        var kind = btn.getAttribute("data-wf-tb");
        if (kind === "toggle") {
          var isNow = tb.classList.toggle("is-collapsed");
          if (window.sessionStorage) window.sessionStorage.setItem("wfTbCollapsed", isNow ? "1" : "0");
          btn.textContent = isNow ? "+" : "−";
          return;
        }
        var value = btn.getAttribute("data-wf-value");
        if (kind === "fx") value = value.toLowerCase();
        self.set(kind, value);
      };
    },

    _group: function (title, key, values, current, labels) {
      var out = '<div class="wf-tb-group"><b>' + title + "</b>";
      for (var i = 0; i < values.length; i++) {
        var v = values[i];
        var label = labels && labels[v] ? labels[v] : v;
        var on = (String(v) === String(current)) ? " is-on" : "";
        out += '<button type="button" class="wf-tb-btn' + on + '" data-wf-tb="' + esc(key) +
          '" data-wf-value="' + esc(v) + '">' + esc(label) + "</button>";
      }
      return out + "</div>";
    },

    _bindBeacons: function () {
      if (this._beaconsBound) return;
      this._beaconsBound = true;
      var self = this;
      document.addEventListener("click", function (e) {
        var el = e.target.closest("[data-beacon]");
        if (el) self.beacon(el.getAttribute("data-beacon"));
      });
    }
  };

  window.WF = WF;
})(window, document);
