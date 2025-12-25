(function () {
  const sessionId = window.__SESSION_ID__;
  if (!sessionId) return;

  const lateMinutes = Number(window.__LATE_MINUTES__ || 10);

  // ✅ Backend'den şunu basacağız:
  // window.__SESSION_STARTED_AT_ISO__ = "2025-12-25T19:10:00" (TR local ISO)
  // window.__TIMEZONE_OFFSET_MIN__ = -180 (TR için genelde -180)
  const sessionStartedIso = window.__SESSION_STARTED_AT_ISO__ || "";
  const tzOffsetMin = Number(window.__TIMEZONE_OFFSET_MIN__ || 0);

  const tbody = document.getElementById("att-table");

  const elPresent = document.getElementById("present-count");
  const elLate = document.getElementById("late-count");
  const elAbsent = document.getElementById("absent-count");

  function badgeHtml(status) {
    const st = (status || "").toUpperCase();
    if (st === "GEÇ" || st === "GEC") {
      return `<span class="px-2 py-1 rounded-full text-xs bg-rose-100 text-rose-700">GEÇ</span>`;
    }
    return `<span class="px-2 py-1 rounded-full text-xs bg-emerald-100 text-emerald-700">ZAMANINDA</span>`;
  }

  // ✅ "Z" varsa UTC gibi davranıp saat kaydırmasın diye normalize ediyoruz.
  // Backend şu an iso + "Z" gönderiyor -> UTC sanılıyor -> TR’de +3 kayıyor.
  // Bu fonksiyon: "2025-12-25T19:10:11.123Z" => "2025-12-25T19:10:11.123"
  function normalizeIso(iso) {
    if (!iso) return "";
    return String(iso).replace(/Z$/i, "");
  }

  // ✅ ISO parse:
  // - "Z" kaldırıldıktan sonra Date bunu local gibi parse eder
  function parseIsoLocal(iso) {
    const fixed = normalizeIso(iso);
    const d = new Date(fixed);
    if (isNaN(d.getTime())) return null;
    return d;
  }

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function formatTRfromDate(d) {
    const dd = pad(d.getDate());
    const mm = pad(d.getMonth() + 1);
    const yy = d.getFullYear();
    const hh = pad(d.getHours());
    const mi = pad(d.getMinutes());
    const ss = pad(d.getSeconds());
    return `${dd}.${mm}.${yy} ${hh}:${mi}:${ss}`;
  }

  function formatTR(iso) {
    const d = parseIsoLocal(iso);
    if (!d) return "";
    return formatTRfromDate(d);
  }

  // ✅ Status hesapla (backend status gelmezse):
  function computeStatus(timestampIso) {
    const t = parseIsoLocal(timestampIso);
    const s = parseIsoLocal(sessionStartedIso);
    if (!t || !s) return "ZAMANINDA";
    const diffMin = (t.getTime() - s.getTime()) / 60000;
    return diffMin > lateMinutes ? "GEÇ" : "ZAMANINDA";
  }

  function upsertRow({ student_no, full_name, timestamp_iso, status }) {
    if (!tbody) return;

    const key = String(student_no || "");
    const rowId = `att-row-${key}`;
    let tr = document.getElementById(rowId);

    const timeTR = formatTR(timestamp_iso);

    const finalStatus = status ? status : computeStatus(timestamp_iso);

    const rowHtml = `
      <td class="p-2">${student_no || ""}</td>
      <td class="p-2">${full_name || ""}</td>
      <td class="p-2">${timeTR}</td>
      <td class="p-2">${badgeHtml(finalStatus)}</td>
    `;

    if (!tr) {
      tr = document.createElement("tr");
      tr.className = "border-t";
      tr.id = rowId;
      tr.innerHTML = rowHtml;
      tbody.prepend(tr);
      return { isNew: true, finalStatus };
    } else {
      tr.innerHTML = rowHtml;
      return { isNew: false, finalStatus };
    }
  }

  function incNumber(el, delta) {
    if (!el) return;
    const cur = Number((el.textContent || "").trim());
    const next = (isNaN(cur) ? 0 : cur) + delta;
    el.textContent = String(next);
  }

  function decNumber(el, delta) {
    incNumber(el, -delta);
  }

  // WS bağlantısı
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const wsUrl = `${proto}://${window.location.host}/ws/session/${sessionId}`;
  const ws = new WebSocket(wsUrl);

  ws.addEventListener("open", () => {
    setInterval(() => {
      if (ws.readyState === 1) ws.send("ping");
    }, 25000);
  });

  ws.addEventListener("message", (evt) => {
    let data;
    try {
      data = JSON.parse(evt.data);
    } catch {
      return;
    }

    const student_no = data.username || "";
    const full_name = data.full_name || "";
    const timestamp_iso = data.timestamp || "";
    let status = data.status || "";

    // ✅ status hiç gelmezse frontend hesaplasın
    if (!status) status = computeStatus(timestamp_iso);

    const { isNew, finalStatus } = upsertRow({ student_no, full_name, timestamp_iso, status });

    if (isNew) {
      // Eğer HTML'de global helper varsa onu kullan (teacher_dashboard.html içinde ekledik)
      if (typeof window.__updateStats === "function") {
        const isLate = String(finalStatus).toUpperCase() === "GEÇ" || String(finalStatus).toUpperCase() === "GEC";
        window.__updateStats(1, isLate ? 1 : 0);
      } else {
        incNumber(elPresent, 1);
        decNumber(elAbsent, 1);
        if (String(finalStatus).toUpperCase() === "GEÇ" || String(finalStatus).toUpperCase() === "GEC") {
          incNumber(elLate, 1);
        }
      }
    }
  });

  ws.addEventListener("close", () => {
    // şimdilik boş
  });
})();
