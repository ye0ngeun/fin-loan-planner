// 계획 관리 화면: 목록·수정·삭제 (F3015, F3016, F3018)
const rows = document.getElementById("rows");

function overlapSet(plans) {
  const ids = new Set();
  for (const a of plans) for (const b of plans) {
    if (a.id !== b.id && a.visit_date === b.visit_date && a.start_time < b.end_time && b.start_time < a.end_time) ids.add(a.id);
  }
  return ids;
}

async function load() {
  try {
    const plans = await api("/api/plans");
    if (!plans.length) {
      rows.innerHTML = `<tr><td colspan="9" class="muted">저장한 계획이 없습니다. 대화 화면에서 추천 상품을 저장해 보세요.</td></tr>`;
      return;
    }
    const overlapped = overlapSet(plans);
    rows.innerHTML = plans.map((p) => `
      <tr data-id="${p.id}">
        <td>${p.id}${overlapped.has(p.id) ? ' <span class="tag fail" title="같은 날짜에 시간이 겹치는 계획이 있습니다">겹침</span>' : ""}</td>
        <td><strong>${esc(p.title)}</strong><br><span class="muted">${esc(p.branch_name || "지점 미정")}${p.branch_address ? " · " + esc(p.branch_address) : ""}</span></td>
        <td><input type="date" class="visit_date" value="${esc(p.visit_date)}"></td>
        <td><input type="time" class="start_time" value="${esc(p.start_time)}"></td>
        <td><input type="time" class="end_time" value="${esc(p.end_time)}"></td>
        <td class="muted">${p.loan_amount_manwon != null ? `${Number(p.loan_amount_manwon).toLocaleString()}만원` : "-"}${p.term_years ? ` · ${p.term_years}년` : ""}<br>${p.annual_rate != null ? `${Number(p.annual_rate).toFixed(2)}% ${esc(p.rate_type || "")}` : ""}${p.monthly_payment_manwon != null ? ` · 월 ${Number(p.monthly_payment_manwon).toLocaleString()}만원` : ""}</td>
        <td><input type="text" class="memo" value="${esc(p.memo || "")}" placeholder="메모" maxlength="500"></td>
        <td class="muted">${esc(p.created_at.replace("T", " "))}${p.updated_at !== p.created_at ? `<br>수정 ${esc(p.updated_at.replace("T", " "))}` : ""}</td>
        <td><button type="button" class="save">수정</button> <button type="button" class="danger del">삭제</button></td>
      </tr>`).join("");
    rows.querySelectorAll("tr[data-id]").forEach((tr) => {
      const id = tr.dataset.id;
      tr.querySelector(".save").onclick = async () => {
        const body = {};
        for (const k of ["visit_date", "start_time", "end_time", "memo"]) body[k] = tr.querySelector("." + k).value;
        try {
          const r = await api(`/api/plans/${id}`, { method: "PUT", body });
          toast(r.message, r.overlaps.length > 0);
          load();
        } catch (e) { toast(e.message, true); }
      };
      tr.querySelector(".del").onclick = async () => {
        if (!confirm(`계획 #${id} 을(를) 삭제할까요?`)) return;
        try { await api(`/api/plans/${id}`, { method: "DELETE" }); toast("삭제했습니다."); load(); }
        catch (e) { toast(e.message, true); }
      };
    });
  } catch (e) {
    rows.innerHTML = `<tr><td colspan="9">${esc(e.message)}</td></tr>`;
  }
}

document.getElementById("refresh").onclick = load;
renderHealth(document.getElementById("health"));
load();
