const state = {
  therapists: [],
  residents: [],
  appointments: [],
  createDuration: 45,
  rescheduleTarget: null,
  rescheduleDuration: 45,
};

function pad(n) {
  return String(n).padStart(2, "0");
}

function getTomorrowLocalSlot(hour, minute) {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(hour, minute, 0, 0);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function isoToLocalInput(iso) {
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function localInputToIso(val) {
  if (!val) return null;
  const d = new Date(val);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

function formatTimeRange(startIso, endIso) {
  const s = new Date(startIso);
  const e = new Date(endIso);
  const datePart = `${s.getMonth() + 1}/${s.getDate()}`;
  return `${datePart} ${pad(s.getHours())}:${pad(s.getMinutes())} – ${pad(e.getHours())}:${pad(e.getMinutes())}`;
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setAlert(id, msg) {
  const el = document.getElementById(id);
  if (!msg) {
    el.classList.add("hidden");
    el.textContent = "";
  } else {
    el.textContent = msg;
    el.classList.remove("hidden");
  }
}

function populateSelects() {
  const createRes = document.getElementById("create-resident");
  const createTh = document.getElementById("create-therapist");
  const filterTh = document.getElementById("filter-therapist");
  const filterRes = document.getElementById("filter-resident");
  const reschTh = document.getElementById("reschedule-therapist");

  createRes.innerHTML = state.residents
    .map((r) => `<option value="${r.id}">${escapeHtml(r.name)} · ${escapeHtml(r.room)}</option>`)
    .join("");

  const thOptions = state.therapists
    .map((t) => `<option value="${t.id}">${escapeHtml(t.name)} · ${escapeHtml(t.specialty)}</option>`)
    .join("");
  createTh.innerHTML = thOptions;
  reschTh.innerHTML = thOptions;

  filterTh.innerHTML =
    `<option value="">全部治疗师</option>` +
    state.therapists.map((t) => `<option value="${t.id}">${escapeHtml(t.name)}</option>`).join("");

  filterRes.innerHTML =
    `<option value="">全部住民</option>` +
    state.residents.map((r) => `<option value="${r.id}">${escapeHtml(r.name)}</option>`).join("");

  document.getElementById("stat-therapists").textContent = String(state.therapists.length);
  if (state.therapists[0]) {
    document.getElementById("race-therapist-name").textContent = state.therapists[0].name;
  }
}

function renderAppointments() {
  const container = document.getElementById("apt-list");
  const scheduled = state.appointments.filter((a) => a.status === "SCHEDULED").length;
  const cancelled = state.appointments.filter((a) => a.status === "CANCELLED").length;
  document.getElementById("stat-scheduled").textContent = String(scheduled);
  document.getElementById("stat-cancelled").textContent = String(cancelled);

  if (state.appointments.length === 0) {
    container.innerHTML = `<div class="empty">当前筛选条件下暂无预约记录。</div>`;
    return;
  }

  container.innerHTML = state.appointments
    .map((apt) => {
      const isScheduled = apt.status === "SCHEDULED";
      return `
        <article class="apt-item ${isScheduled ? "" : "cancelled"}">
          <div>
            <div class="apt-meta">
              <span class="apt-time">${formatTimeRange(apt.startTime, apt.endTime)}</span>
              <span class="badge ${isScheduled ? "badge-scheduled" : "badge-cancelled"}">
                ${isScheduled ? "已排期" : "已取消"}
              </span>
              <span class="badge badge-version">${apt.durationMinutes} 分钟 · v${apt.version}</span>
            </div>
            <div class="apt-details">
              住民：<strong>${escapeHtml(apt.residentName)}</strong>（${escapeHtml(apt.residentRoom)}） ·
              治疗师：<strong>${escapeHtml(apt.therapistName)}</strong>（${escapeHtml(apt.therapistSpecialty)}）
            </div>
            ${apt.notes ? `<div class="apt-notes">${escapeHtml(apt.notes)}</div>` : ""}
          </div>
          ${
            isScheduled
              ? `<div class="apt-actions">
                   <button type="button" class="btn btn-secondary btn-sm" data-action="reschedule" data-id="${apt.id}">改期</button>
                   <button type="button" class="btn btn-danger btn-sm" data-action="cancel" data-id="${apt.id}">取消</button>
                 </div>`
              : ""
          }
        </article>
      `;
    })
    .join("");
}

async function fetchMeta() {
  const res = await fetch("/api/meta", { cache: "no-store" });
  if (!res.ok) throw new Error("无法加载基础数据");
  const data = await res.json();
  state.therapists = data.therapists || [];
  state.residents = data.residents || [];
  populateSelects();
}

async function fetchAppointments() {
  setAlert("list-error", null);
  const status = document.getElementById("filter-status").value;
  const therapistId = document.getElementById("filter-therapist").value;
  const residentId = document.getElementById("filter-resident").value;

  const params = new URLSearchParams();
  if (status !== "ALL") params.set("status", status);
  if (therapistId) params.set("therapistId", therapistId);
  if (residentId) params.set("residentId", residentId);

  const res = await fetch(`/api/appointments?${params.toString()}`, { cache: "no-store" });
  if (!res.ok) throw new Error("无法加载预约清单");
  const data = await res.json();
  state.appointments = data.appointments || [];
  renderAppointments();
}

function bindDurationGroup(groupId, onSelect) {
  const group = document.getElementById(groupId);
  group.addEventListener("click", (e) => {
    const btn = e.target.closest(".duration-btn");
    if (!btn) return;
    const mins = Number(btn.dataset.mins);
    group.querySelectorAll(".duration-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    onSelect(mins);
  });
}

function openRescheduleModal(apt) {
  state.rescheduleTarget = apt;
  state.rescheduleDuration = apt.durationMinutes;

  document.getElementById("reschedule-version").textContent = `v${apt.version}`;
  document.getElementById("reschedule-subtitle").innerHTML =
    `住民：<strong>${escapeHtml(apt.residentName)}</strong>（${escapeHtml(apt.residentRoom)}）。若新时段冲突，事务将自动回滚，原预约完整保留。`;
  document.getElementById("reschedule-therapist").value = apt.therapistId;
  document.getElementById("reschedule-start").value = isoToLocalInput(apt.startTime);
  document.getElementById("reschedule-notes").value = apt.notes || "";

  const group = document.getElementById("reschedule-duration-group");
  group.querySelectorAll(".duration-btn").forEach((b) => {
    b.classList.toggle("active", Number(b.dataset.mins) === apt.durationMinutes);
  });

  setAlert("reschedule-error", null);
  document.getElementById("reschedule-backdrop").classList.remove("hidden");
}

function closeRescheduleModal() {
  state.rescheduleTarget = null;
  document.getElementById("reschedule-backdrop").classList.add("hidden");
}

async function handleCancel(apt) {
  const ok = window.confirm(
    `确认取消 ${apt.residentName} 与 ${apt.therapistName} 的预约吗？（取消后该时段将自动释放，记录保留为已取消状态）`
  );
  if (!ok) return;

  const res = await fetch(`/api/appointments/${apt.id}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expectedVersion: apt.version }),
  });
  const data = await res.json();
  if (!res.ok) {
    setAlert("list-error", data?.error?.message || "取消失败，请刷新后重试。");
    return;
  }
  await fetchAppointments();
}

async function runConcurrencyDemo() {
  if (state.residents.length < 2 || state.therapists.length < 1) return;
  const startTime = localInputToIso(document.getElementById("race-slot").value);
  if (!startTime) return;

  const btn = document.getElementById("race-btn");
  const logEl = document.getElementById("race-log");
  btn.disabled = true;
  btn.textContent = "并发请求执行中...";
  logEl.classList.add("hidden");

  const therapist = state.therapists[0];
  const rA = state.residents[3] || state.residents[0];
  const rB = state.residents[4] || state.residents[1];

  const fire = async (staff, resident) => {
    const res = await fetch("/api/appointments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        residentId: resident.id,
        therapistId: therapist.id,
        startTime,
        durationMinutes: 30,
        notes: `[并发抢订演示] ${staff}`,
      }),
    });
    const data = await res.json();
    if (res.ok) {
      return {
        staff,
        residentName: resident.name,
        status: res.status,
        ok: true,
        code: "CREATED",
        message: `抢订成功（ID: ${data.appointment.id}）`,
      };
    }
    return {
      staff,
      residentName: resident.name,
      status: res.status,
      ok: false,
      code: data?.error?.code || "ERROR",
      message: data?.error?.message || "请求被拦截",
    };
  };

  try {
    const results = await Promise.all([fire("员工 A", rA), fire("员工 B", rB)]);
    logEl.innerHTML = results
      .map(
        (r) => `
          <div class="race-row ${r.ok ? "ok" : "conflict"}">
            <strong>${escapeHtml(r.staff)}（住民：${escapeHtml(r.residentName)}）· HTTP ${r.status} [${escapeHtml(r.code)}]</strong>
            <div>${escapeHtml(r.message)}</div>
          </div>
        `
      )
      .join("");
    logEl.classList.remove("hidden");
    await fetchAppointments();
  } finally {
    btn.disabled = false;
    btn.textContent = "同时发起 2 笔抢订请求";
  }
}

async function init() {
  document.getElementById("create-start").value = getTomorrowLocalSlot(14, 0);
  document.getElementById("race-slot").value = getTomorrowLocalSlot(16, 0);

  bindDurationGroup("create-duration-group", (mins) => {
    state.createDuration = mins;
  });
  bindDurationGroup("reschedule-duration-group", (mins) => {
    state.rescheduleDuration = mins;
  });

  document.getElementById("create-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setAlert("create-error", null);
    setAlert("create-success", null);

    const startTime = localInputToIso(document.getElementById("create-start").value);
    if (!startTime) {
      setAlert("create-error", "请选择有效的开始时间。");
      return;
    }

    const submitBtn = document.getElementById("create-submit");
    submitBtn.disabled = true;
    try {
      const res = await fetch("/api/appointments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          residentId: document.getElementById("create-resident").value,
          therapistId: document.getElementById("create-therapist").value,
          startTime,
          durationMinutes: state.createDuration,
          notes: document.getElementById("create-notes").value,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setAlert("create-error", data?.error?.message || "新增预约失败。");
        return;
      }
      setAlert("create-success", "预约已创建并保存至 SQLite 数据库。");
      document.getElementById("create-notes").value = "";
      await fetchAppointments();
    } finally {
      submitBtn.disabled = false;
    }
  });

  document.getElementById("reschedule-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.rescheduleTarget) return;
    setAlert("reschedule-error", null);

    const startTime = localInputToIso(document.getElementById("reschedule-start").value);
    if (!startTime) {
      setAlert("reschedule-error", "请选择有效的改期时间。");
      return;
    }

    const submitBtn = document.getElementById("reschedule-submit");
    submitBtn.disabled = true;
    try {
      const res = await fetch(`/api/appointments/${state.rescheduleTarget.id}/reschedule`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          therapistId: document.getElementById("reschedule-therapist").value,
          startTime,
          durationMinutes: state.rescheduleDuration,
          notes: document.getElementById("reschedule-notes").value,
          expectedVersion: state.rescheduleTarget.version,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        const baseMsg = data?.error?.message || "改期失败。";
        const msg = baseMsg.includes("原预约")
          ? baseMsg
          : `${baseMsg}（原预约已完整保留）`;
        setAlert("reschedule-error", msg);
        return;
      }
      closeRescheduleModal();
      await fetchAppointments();
    } finally {
      submitBtn.disabled = false;
    }
  });

  document.getElementById("reschedule-close").addEventListener("click", closeRescheduleModal);
  document.getElementById("race-btn").addEventListener("click", runConcurrencyDemo);
  document.getElementById("refresh-btn").addEventListener("click", () => fetchAppointments());

  ["filter-status", "filter-therapist", "filter-resident"].forEach((id) => {
    document.getElementById(id).addEventListener("change", () => fetchAppointments());
  });

  document.getElementById("apt-list").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const apt = state.appointments.find((a) => a.id === btn.dataset.id);
    if (!apt) return;
    if (btn.dataset.action === "reschedule") openRescheduleModal(apt);
    if (btn.dataset.action === "cancel") handleCancel(apt);
  });

  try {
    await fetchMeta();
    await fetchAppointments();
  } catch (err) {
    setAlert("list-error", err.message || "初始化失败");
  }
}

init();
