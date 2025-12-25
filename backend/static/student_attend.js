(function () {
  const cfg = window.__ATTEND__;
  if (!cfg) return;

  const $countdown = document.getElementById("countdown");

  const $box = document.getElementById("statusBox");
  const $icon = document.getElementById("statusIcon");
  const $title = document.getElementById("statusTitle");
  const $desc = document.getElementById("statusDesc");
  const $meta = document.getElementById("statusMeta");

  const startedAt = new Date(cfg.startedAt);
  const expiresAt = new Date(cfg.expiresAt);
  const graceMs = (cfg.graceMinutes || 10) * 60 * 1000;

  let alreadySent = false;

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function setStatus(kind, title, desc, meta) {
    // kind: "loading" | "ok" | "warn" | "err" | "closed"
    const styles = {
      loading: { bg: "bg-slate-50", iconBg: "bg-slate-400", icon: "…" },
      ok: { bg: "bg-emerald-50", iconBg: "bg-emerald-500", icon: "✓" },
      warn: { bg: "bg-amber-50", iconBg: "bg-amber-500", icon: "!" },
      err: { bg: "bg-rose-50", iconBg: "bg-rose-500", icon: "×" },
      closed: { bg: "bg-slate-50", iconBg: "bg-slate-700", icon: "⏱" },
    };

    const s = styles[kind] || styles.loading;

    $box.className = "mt-6 rounded-xl border p-4 " + s.bg;
    $icon.className = "w-8 h-8 rounded-full flex items-center justify-center text-white " + s.iconBg;
    $icon.textContent = s.icon;

    $title.textContent = title || "";
    $desc.textContent = desc || "";
    $meta.textContent = meta || "";
  }

  function fmtTR(dt) {
    // dd.MM.yyyy HH:mm:ss
    const d = pad(dt.getDate());
    const m = pad(dt.getMonth() + 1);
    const y = dt.getFullYear();
    const hh = pad(dt.getHours());
    const mm = pad(dt.getMinutes());
    const ss = pad(dt.getSeconds());
    return `${d}.${m}.${y} ${hh}:${mm}:${ss}`;
  }

  function updateCountdown() {
    const now = new Date();
    const ms = expiresAt - now;

    if (ms <= 0) {
      $countdown.textContent = "00:00";
      // Oturum kapandı görünümü
      setStatus(
        "closed",
        "Oturum kapandı",
        "Bu yoklama oturumunun süresi doldu. Hoca yeni oturum açarsa tekrar deneyebilirsin.",
        `Bitiş: ${fmtTR(expiresAt)}`
      );
      return false; // stop
    }

    const totalSec = Math.floor(ms / 1000);
    const min = Math.floor(totalSec / 60);
    const sec = totalSec % 60;
    $countdown.textContent = `${pad(min)}:${pad(sec)}`;
    return true;
  }

  async function doCheckin() {
    if (alreadySent) return;
    alreadySent = true;

    // Oturum süresi dolmuşsa hiç POST atma
    const now = new Date();
    if (now > expiresAt) {
      setStatus(
        "closed",
        "Oturum kapandı",
        "Bu yoklama oturumu kapalı veya süresi dolmuş.",
        `Bitiş: ${fmtTR(expiresAt)}`
      );
      return;
    }

    setStatus("loading", "İşleniyor…", "Yoklama kaydın alınıyor.", "");

    try {
      const res = await fetch(`/s/${encodeURIComponent(cfg.sessionCode)}/checkin`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: ""
      });

      const text = await res.text();

      // Başarılı kayıt / zaten kayıtlı mesajlarını yakala (backend HTML dönüyor)
      const okLike =
        res.ok &&
        (text.includes("başarıyla") || text.includes("✅") || text.includes("Zaten"));

      const now2 = new Date();
      const isLate = (now2 - startedAt) > graceMs;

      if (okLike) {
        // "Zaten yoklamaya katıldın." da ok sayılır ama ikon warn yapalım
        if (text.toLowerCase().includes("zaten")) {
          setStatus(
            "warn",
            "Zaten yoklama alınmış",
            "Bu oturuma daha önce katılmışsın. Tekrar işlem yapılmadı.",
            `Durum: ${isLate ? "GEÇ" : "ZAMANINDA"} • Zaman: ${fmtTR(now2)}`
          );
        } else {
          setStatus(
            "ok",
            "Yoklama alındı",
            "Yoklamaya başarıyla katıldın.",
            `Durum: ${isLate ? "GEÇ" : "ZAMANINDA"} • Zaman: ${fmtTR(now2)}`
          );
        }
        return;
      }

      // Oturum kapalı/doldu vs.
      if (text.toLowerCase().includes("kapalı") || text.toLowerCase().includes("süresi dolmuş")) {
        setStatus(
          "closed",
          "Oturum kapandı",
          "Bu yoklama oturumu kapalı veya süresi dolmuş.",
          `Bitiş: ${fmtTR(expiresAt)}`
        );
        return;
      }

      // Diğer hatalar
      setStatus("err", "Hata", "Yoklama alınamadı. Sayfayı yenileyip tekrar dene.", text.slice(0, 200));
    } catch (e) {
      setStatus("err", "Hata", "Bağlantı hatası oluştu. İnterneti kontrol edip sayfayı yenile.", String(e));
    }
  }

  // 1) Countdown başlat
  const timer = setInterval(() => {
    const ok = updateCountdown();
    if (!ok) clearInterval(timer);
  }, 1000);
  updateCountdown();

  // 2) Sayfa açılır açılmaz yoklamayı al
  doCheckin();
})();
