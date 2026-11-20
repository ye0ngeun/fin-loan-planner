// 대화 화면: /api/chat 호출, 답변·추천 카드·실행 과정 표시 (F3018, F3019)
const STAGES = { analyze: "① 요청 분석", plan: "② 작업 계획", execute: "③ Tool 실행", judge: "④ 제약 판단", replan: "↻ 재계획", respond: "⑤ 응답 생성" };
const TOOL_NAMES = {
  search_mortgage_products: "상품 후보 조회 (금감원)",
  get_mortgage_product_detail: "상품 상세 조회 (금감원)",
  get_apartment_prices: "실거래 시세 조회 (국토부)",
  search_bank_branches: "은행 지점 검색 (카카오)",
  search_loan_docs: "용어 문서 검색 (2회차 색인)",
};
const $ = (id) => document.getElementById(id);
let sessionId = null;
try { sessionId = sessionStorage.getItem("planner_session"); } catch (e) {}

function addMessage(cls, html) {
  const el = document.createElement("div");
  el.className = `msg ${cls}`;
  el.innerHTML = html;
  $("messages").appendChild(el);
  $("messages").scrollTop = $("messages").scrollHeight;
  return el;
}

function argText(args) {
  return Object.entries(args || {}).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : v}`).join(", ");
}

function renderTrace(trace, elapsed) {
  const ul = $("timeline");
  ul.innerHTML = "";
  const stageRows = {};
  const requests = {};
  for (const e of trace) {
    const p = e.payload;
    let li = null;
    if (e.event === "stage.enter") {
      li = document.createElement("li");
      li.className = p.stage === "replan" ? "replan" : "stage";
      li.textContent = STAGES[p.stage] || p.stage;
      stageRows[p.stage] = li;
    } else if (e.event === "stage.exit" && stageRows[p.stage]) {
      stageRows[p.stage].innerHTML += ` <span class="muted">${p.elapsed_ms} ms</span>`;
    } else if (e.event === "decision") {
      li = document.createElement("li");
      li.className = "decision";
      let body = `<span class="tag ${p.by}">${p.by === "llm" ? "LLM 판단" : "규칙"}</span>`;
      if (p.kind === "intent") body += `의도 <strong>${esc(p.intent)}</strong> — ${esc(p.reason)}${p.tools?.length ? `<br><span class="muted">고른 Tool: ${esc(p.tools.join(", "))}</span>` : ""}`;
      else if (p.kind === "plan") {
        body += esc(p.reason);
        if (p.steps?.length) body += "<br>" + p.steps.map((s) => `${s.id}. ${esc(TOOL_NAMES[s.tool] || s.tool)}${s.bind ? ` <span class="muted">← ${esc(s.bind === "each_product" ? "후보 product_id 마다" : "1순위 금융회사")}</span>` : ""}`).join("<br>");
        if (p.missing?.length) body += `<br><span class="muted">모자란 조건: ${esc(p.missing.join(", "))}</span>`;
        if (p.defaults?.length) body += `<br><span class="muted">${esc(p.defaults.join(" "))}</span>`;
      } else if (p.kind === "next") body += `다음: <strong>${esc(p.next)}</strong> — ${esc(p.reason)} <span class="muted">(충족 ${p.satisfied} / 제외 ${p.excluded})</span>`;
      else body += esc(p.reason || p.kind);
      li.innerHTML = body;
    } else if (e.event === "tool.request") {
      requests[p.step + p.tool] = p;
    } else if (e.event === "tool.response") {
      const req = requests[p.step + p.tool] || {};
      li = document.createElement("li");
      li.className = p.ok ? "tool-ok" : "tool-fail";
      li.innerHTML = `<span class="tag ${p.ok ? "ok" : "fail"}">${p.ok ? "성공" : esc(p.code)}</span>${esc(TOOL_NAMES[p.tool] || p.tool)} <span class="muted">${p.elapsed_ms} ms${req.round ? ` · 재계획 ${req.round}차` : ""}</span><br><span class="muted">${esc(argText(req.args))}</span><br>${esc(p.summary)}`;
    } else if (e.event === "tool.reuse") {
      li = document.createElement("li");
      li.className = "reuse";
      li.textContent = `재사용: ${TOOL_NAMES[p.tool] || p.tool} (${argText(p.args)}) — 이미 받은 결과`;
    } else if (e.event === "replan") {
      li = document.createElement("li");
      li.className = "replan";
      li.textContent = `재계획 ${p.n}/${p.max}: ${p.reason}`;
    } else if (e.event === "error") {
      li = document.createElement("li");
      li.className = "error";
      li.textContent = `오류: ${STAGES[p.stage] || p.stage} 단계에서 문제가 생겨 건너뛰었습니다.`;
    }
    if (li) ul.appendChild(li);
  }
  $("trace-meta").textContent = `이벤트 ${trace.length}개 · ${elapsed} ms`;
}

function renderConditions(view) {
  const c = view.conditions || {};
  const changed = new Set(view.changed || []);
  const block = (title, obj) => {
    const entries = Object.entries(obj || {});
    if (!entries.length) return `<dt>${title}</dt><dd>없음</dd>`;
    return `<dt>${title}</dt>` + entries.map(([k, v]) => `<dd>${changed.has(k) ? "<strong>" : ""}${esc(k)}: ${esc(Array.isArray(v) ? v.join(", ") : v)}${changed.has(k) ? " (변경)</strong>" : ""}</dd>`).join("");
  };
  $("conditions").innerHTML = view.intent ? `<dl>${block("기준 조건", c["기준"])}${block("선호 조건", c["선호"])}${block("제약 조건", c["제약"])}</dl>` : "";
}

function candidateCards(cands) {
  if (!cands?.length) return "";
  const today = new Date().toISOString().slice(0, 10);
  return `<div class="cards">${cands.map((c) => `
    <div class="card" data-rank="${c.rank}">
      <div class="row"><strong>${c.rank}. ${esc(c.name)}</strong><span>월 ${c.monthly_payment.toLocaleString()}만원 · ${c.rate.toFixed(2)}% ${esc(c.rate_type)}</span></div>
      <div class="row"><span class="muted">대출금 ${c.principal.toLocaleString()}만원 · ${c.term_years}년${c.branch_name ? " · " + esc(c.branch_name) : ""}</span>
        <button class="ghost open-save" type="button">이 상품으로 상담 계획 저장</button></div>
      <div class="save-form"><input type="date" class="d" value="${today}"><input type="time" class="s" value="14:00"><input type="time" class="e" value="15:00"><button type="button" class="do-save">저장</button></div>
    </div>`).join("")}</div>`;
}

function bindCards(el, cands) {
  el.querySelectorAll(".card").forEach((card) => {
    const c = cands.find((x) => String(x.rank) === card.dataset.rank);
    card.querySelector(".open-save").onclick = () => card.querySelector(".save-form").classList.toggle("open");
    card.querySelector(".do-save").onclick = async () => {
      try {
        const res = await api("/api/plans", {
          method: "POST",
          body: {
            title: `${c.name} 상담`, product_id: c.product_id, kor_co_nm: c.kor_co_nm, fin_prdt_nm: c.fin_prdt_nm,
            branch_name: c.branch_name, branch_address: c.branch_address,
            visit_date: card.querySelector(".d").value, start_time: card.querySelector(".s").value, end_time: card.querySelector(".e").value,
            loan_amount_manwon: c.principal, term_years: c.term_years, rate_type: c.rate_type, rpay_type: c.rpay_type,
            annual_rate: c.rate, monthly_payment_manwon: c.monthly_payment, session_id: sessionId,
          },
        });
        toast(res.message, res.overlaps.length > 0);
        card.querySelector(".save-form").classList.remove("open");
      } catch (e) { toast(e.message, true); }
    };
  });
}

async function send(text) {
  const btn = $("send");
  btn.disabled = true;
  $("input").value = "";
  addMessage("user", esc(text));
  const pending = addMessage("bot", `<span class="spinner"></span>처리 중… 요청을 분석하고 필요한 Tool 을 부르고 있습니다.`);
  $("status").textContent = "";
  try {
    const r = await api("/api/chat", { method: "POST", body: { message: text, session_id: sessionId } });
    sessionId = r.session_id;
    try { sessionStorage.setItem("planner_session", sessionId); } catch (e) {}
    let html = md(r.answer);
    if (r.view.sources?.length) html += `<p class="muted">출처: ${r.view.sources.map((s) => `[근거 ${s.n}] ${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>` : esc(s.title)} (${esc(s.source)})`).join(" · ")}</p>`;
    if (r.error?.code === "LLM_KEY_MISSING") html += `<p class="muted">설정: 저장소 루트 <code>.env</code> 의 OPENAI_API_KEY</p>`;
    html += candidateCards(r.candidates);
    html += `<div class="meta">의도 ${esc(r.intent || "-")} · Tool 호출 ${r.view.calls?.filter((c) => !c.reused).length || 0}회 · 재계획 ${r.view.replans?.length || 0}회 · ${r.elapsed_ms} ms</div>`;
    pending.innerHTML = html;
    bindCards(pending, r.candidates);
    renderTrace(r.trace, r.elapsed_ms);
    renderConditions(r.view);
    renderNotices(r.notices);
    const fails = (r.view.tool_failures || []).filter((c) => c.code === "INVALID_KEY");
    if (fails.length) toast(`API 키 확인 필요: ${fails.map((c) => TOOL_NAMES[c.tool] || c.tool).join(", ")}`, true);
  } catch (e) {
    pending.innerHTML = `<p>${esc(e.message)}</p>`;
  } finally {
    btn.disabled = false;
    $("messages").scrollTop = $("messages").scrollHeight;
  }
}

$("ask").addEventListener("submit", (e) => { e.preventDefault(); const t = $("input").value.trim(); if (t) send(t); });
$("input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("ask").requestSubmit(); } });
document.querySelectorAll("#examples .chip").forEach((b) => (b.onclick = () => send(b.textContent)));
$("reset").onclick = async () => {
  if (sessionId) await api(`/api/chat/${sessionId}`, { method: "DELETE" }).catch(() => {});
  sessionId = null;
  try { sessionStorage.removeItem("planner_session"); } catch (e) {}
  $("messages").innerHTML = "";
  addMessage("bot", "새 대화를 시작합니다. 조건을 알려 주세요.");
  $("timeline").innerHTML = ""; $("conditions").innerHTML = ""; $("trace-meta").textContent = "";
};
renderNotices(DEFAULT_NOTICES);
renderHealth($("health")).then((h) => h && renderNotices(h.notices));
