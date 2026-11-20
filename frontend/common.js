// 공통: API 호출, 안전한 마크다운 표시, 설정 안내, 면책·출처 문구 (F3018, F3019, NF3002)
// 백엔드를 다른 주소에서 띄웠으면 config.js 의 window.API_BASE 를 바꾼다.
const API = (window.API_BASE || "").replace(/\/$/, "");

async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(API + path, {
      headers: { "Content-Type": "application/json" },
      ...options,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
  } catch (e) {
    throw new Error("서버에 연결하지 못했습니다. 백엔드가 실행 중인지 확인하세요.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    // 오류 상세(스택 등)는 보여 주지 않고 요약만 (NF3002)
    const detail = typeof data.detail === "string" ? data.detail : res.status === 422 ? "입력값을 확인해 주세요." : "요청을 처리하지 못했습니다.";
    throw new Error(detail);
  }
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// 굵게·링크·목록·줄바꿈만 지원하는 작은 마크다운 변환기. 먼저 이스케이프한다.
function md(text) {
  const lines = esc(text).split("\n");
  let html = "", list = null;
  const inline = (s) =>
    s
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  for (const raw of lines) {
    const m = raw.match(/^(\s*)(-|\d+\.)\s+(.*)$/);
    if (m) {
      const type = m[2] === "-" ? "ul" : "ol";
      // 번호 목록 사이에 하위 항목이 끼어도 번호가 1로 돌아가지 않도록 시작 번호를 적는다
      const start = type === "ol" ? ` start="${parseInt(m[2], 10)}"` : "";
      if (list !== type) { if (list) html += `</${list}>`; html += `<${type}${start}>`; list = type; }
      html += `<li>${inline(m[3])}</li>`;
    } else {
      if (list) { html += `</${list}>`; list = null; }
      if (raw.trim()) html += `<p>${inline(raw)}</p>`;
    }
  }
  if (list) html += `</${list}>`;
  return html;
}

function toast(msg, warn = false) {
  let el = document.querySelector(".toast");
  if (!el) { el = document.createElement("div"); el.className = "toast"; document.body.appendChild(el); }
  el.textContent = msg;
  el.className = "toast show" + (warn ? " warn" : "");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), warn ? 7000 : 3500);
}

const KEY_GUIDE = {
  llm: ["OPENAI_API_KEY", "LLM(SSAFY GMS)", "요청 분석을 할 수 없습니다"],
  fss: ["FSS_API_KEY", "금감원 금융상품한눈에", "상품 후보·금리를 조회할 수 없습니다"],
  molit: ["MOLIT_API_KEY", "국토부 실거래가", "집값을 말하지 않으면 시세로 대출금을 정할 수 없습니다"],
  kakao: ["KAKAO_REST_API_KEY", "카카오 로컬", "은행 지점 안내가 빠집니다"],
};

async function renderHealth(target) {
  try {
    const h = await api("/api/health");
    const missing = Object.entries(h.keys).filter(([, v]) => !v).map(([k]) => k);
    const parts = [];
    if (h.fake_apis || h.fake_llm) {
      parts.push(`<div class="banner info">연습용 모드입니다 (${[h.fake_apis && "외부 API → 가상 응답", h.fake_llm && "LLM → 규칙 대역"].filter(Boolean).join(", ")}). 화면의 금리·시세는 실제 공시값이 아닙니다.</div>`);
    }
    const real = missing.filter((k) => !(h.fake_apis && k !== "llm") && !(h.fake_llm && k === "llm"));
    if (real.length) {
      const bad = real.includes("llm");
      parts.push(
        `<div class="banner ${bad ? "bad" : "warn"}"><strong>API 키 설정 안내</strong> — ` +
          real.map((k) => `${KEY_GUIDE[k][1]} 키(<code>${KEY_GUIDE[k][0]}</code>)가 없어 ${KEY_GUIDE[k][2]}.`).join(" ") +
          ` 저장소 루트의 <code>.env</code>에 키를 넣고 서버를 다시 시작하세요.</div>`
      );
    }
    target.innerHTML = parts.join("");
    return h;
  } catch (e) {
    target.innerHTML = `<div class="banner bad">${esc(e.message)}</div>`;
    return null;
  }
}

function renderNotices(notices) {
  const el = document.querySelector("footer.notices");
  if (!el || !notices) return;
  el.innerHTML = `<p>${esc(notices.disclaimer)}</p><p>${esc(notices.fss_source)}</p><p>${esc(notices.molit_source)}</p>`;
}

// 서버가 없어도 문구는 보이도록 기본값을 둔다 (서버 응답으로 덮어씀)
const DEFAULT_NOTICES = {
  disclaimer: "본 서비스는 정보 제공용이며 금융 전문가의 조언을 대신하지 않습니다. 실제 금리·한도는 금융회사 심사로 정해집니다.",
  fss_source: "상품 정보는 금융상품통합비교공시시스템(금융감독원) 금융회사 상품정보를 사용한 결과이며, 금융감독원은 정보의 정확성·최신성을 보증하지 않습니다.",
  molit_source: "시세는 국토교통부 아파트 매매 실거래가(공공데이터포털)의 과거 거래 사례이며 현재 매물이 아닙니다.",
};
