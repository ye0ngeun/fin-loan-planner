// 실행 로그 화면: 세션 목록·상세(실행 순서)·삭제 (F3014)
const CLS = { "stage.enter": "stage", "tool.response": "tool-ok", decision: "decision", replan: "replan", error: "error", "tool.reuse": "reuse" };

async function loadSessions() {
  const tbody = document.getElementById("sessions");
  try {
    const list = await api("/api/logs");
    if (!list.length) { tbody.innerHTML = `<tr><td colspan="7" class="muted">저장된 실행 로그가 없습니다.</td></tr>`; return; }
    tbody.innerHTML = list.map((s) => `
      <tr><td><a href="#" data-id="${esc(s.session_id)}" class="open">${esc(s.session_id)}</a><br><span class="muted">${esc(s.started_at.replace("T", " ").slice(0, 19))}</span></td>
      <td>${esc(s.first_message)}</td><td>${s.turns}</td><td>${s.tool_calls}</td><td>${s.replans}</td><td>${s.tool_failures}</td>
      <td><button type="button" class="danger del" data-id="${esc(s.session_id)}">삭제</button></td></tr>`).join("");
    tbody.querySelectorAll(".open").forEach((a) => (a.onclick = (e) => { e.preventDefault(); loadDetail(a.dataset.id); }));
    tbody.querySelectorAll(".del").forEach((b) => (b.onclick = async () => {
      if (!confirm("이 세션의 실행 로그를 삭제할까요?")) return;
      try { const r = await api(`/api/logs/${b.dataset.id}`, { method: "DELETE" }); toast(`이벤트 ${r.deleted}개를 삭제했습니다.`); loadSessions(); }
      catch (e) { toast(e.message, true); }
    }));
  } catch (e) { tbody.innerHTML = `<tr><td colspan="7">${esc(e.message)}</td></tr>`; }
}

async function loadDetail(id) {
  const ol = document.getElementById("events");
  try {
    const d = await api(`/api/logs/${id}`);
    document.getElementById("detail-meta").textContent = `${id} · 이벤트 ${d.events.length}개`;
    ol.innerHTML = d.events.map((e) => {
      let cls = CLS[e.event] || "";
      if (e.event === "tool.response" && !e.payload.ok) cls = "tool-fail";
      return `<li class="${cls}"><span class="tag">${e.turn}-${e.seq}</span><strong>${esc(e.event)}</strong> <span class="muted">${esc(e.ts.slice(11))}</span><pre class="json">${esc(JSON.stringify(e.payload, null, 1))}</pre></li>`;
    }).join("");
  } catch (e) { ol.innerHTML = `<li>${esc(e.message)}</li>`; }
}

document.getElementById("refresh").onclick = loadSessions;
loadSessions();
