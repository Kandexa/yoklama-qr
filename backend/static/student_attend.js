(function () {
  const data = window.__ATTEND__;
  if (!data || !data.sessionCode) return;

  const sessionCode = data.sessionCode;
  const startedAt = new Date(data.startedAt).getTime();
  const expiresAt = new Date(data.expiresAt).getTime();
  const graceMinutes = Number(data.graceMinutes || 10);

  const elCountdown = document.getElementById("countdown");
  const box = document.getElementById("statusBox");
  const icon = document.getElementById("statusIcon");
  const title = document.getElementById("statusTitle");
  const desc = document.getElementById("statusDesc");
  const meta = document.getElementById("statusMeta");

  function pad(n) { return String(n).padStart(2, "0"); }

  function fmtTR(ms) {
    try {
      // Tarayıcı TR saatini doğru gösterecek
      return new Intl.DateTimeFormat("tr-TR", {
        timeZone: "Europe/Istanbul",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
      }).format(new Date(ms));
    } catch {
      const d = new Date(ms);
      return `${pad(d.getDate())}.${pad(d.getMonth()+1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    }
  }

  function setStatus(state, text, detail, whenMs) {
    // state: "loading" | "ok" | "late" | "error"
    if (state === "loading") {
      icon.textContent = "…";
      icon.className = "w-8 h-8 rounded-full flex items-center justify-center text-white bg-slate-400";
    } else if (state === "ok") {
      icon.textContent = "✓";
      icon.className = "w-8 h-8 rounded-full flex items-center justify-center text-white bg-emerald-600";
    } else if (state === "late") {
      icon.textContent = "!";
      icon.className = "w-8 h-8 rounded-full flex items-center justify-center text-white bg-rose-600";
    } else {
      icon.textContent = "×";
      icon.className = "w-8 h-8 rounded-full flex items-center justify-center text-white bg-slate-700";
    }

    title.textContent = text;
    desc.textContent = detail || "";

    if (whenMs) {
      meta.textContent = `Zaman: ${fmtTR(whenMs)}`;
    } else {
      meta.textContent = "";
    }
  }

  // ✅ Countdown
  function tick() {
    const now = Date.now();
    let diff = expiresAt - now;
    if (diff < 0) diff = 0;

    const sec = Math.floor(diff / 1000);
    const m = Math.floor(sec / 60);
    const s = sec % 60;

    if (elCountdown) elCountdown.textContent = `${pad(m)}:${pad(s)}`;

    if (diff > 0) setTimeout(tick, 1000);
  }
  tick();

  // ✅ Yoklama al
  async function checkin() {
    setStatus("loading", "İşleniyor…", "Yoklama kaydın alınıyor, lütfen bekle.");

    const url = `/s/${sessionCode}/checkin`;

    try {
      const res = await fetch(url, {
        method: "POST",
        credentials: "include"
      });

      // ✅ Eğer login'e yönlendirdiyse (cookie yoksa)
      if (res.redirected) {
        window.location.href = res.url;
        return;
      }

      const text = await res.text();

      // ✅ başarılıysa status'u client-side hesaplayıp gösterelim
      const now = Date.now();
      const diffMin = (now - startedAt) / 60000.0;
      const st = diffMin > graceMinutes ? "GEÇ" : "ZAMANINDA";

      if (text.includes("✅") || text.toLowerCase().includes("yoklama") || text.toLowerCase().includes("zaten")) {
        if (st === "GEÇ") {
          setStatus("late", "Yoklama alındı (GEÇ)", "Yoklamaya katıldın.", now);
        } else {
          setStatus("ok", "Yoklama alındı", "Yoklamaya başarıyla katıldın.", now);
        }
      } else {
        setStatus("error", "Hata", text || "İşlem başarısız.", now);
      }
    } catch (e) {
      setStatus("error", "Bağlantı Hatası", "Sunucuya ulaşılamadı. Sayfayı yenileyip tekrar dene.");
    }
  }

  checkin();
})();
