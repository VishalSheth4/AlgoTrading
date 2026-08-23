/*
Shared header behavior for every algoTrader page: NY/IST clocks, live bid/
ask price polling, the alert flash+buzz effect, the Alerts/Auto-Trade
toggle buttons, and the Test Alert button. Used by core/_header.html on
every page so the header looks AND behaves identically everywhere.

Pages that actually generate alerts (the live Dashboard, the XAUUSD Chart)
call `AppHeader.checkAlerts(alerts)` on every poll with that page's own
`data.alerts` array. Pages that don't (Backtest, Settings) simply never
call it -- the header still ticks/polls price/toggles Auto Trade, it just
never flashes, which is correct: there's nothing to alert on there.
*/
(function () {
  const ALERT_LABELS = {
    supertrend_flip: (a) => `${a.timeframe} flipped ${a.direction === "bullish" ? "▲ BULLISH" : "▼ BEARISH"}`,
    green_dollar: (a) => `${a.timeframe} Dollar Signal ${a.direction === "bullish" ? "▲ BULLISH" : "▼ BEARISH"}`,
    greenDollar: (a) => `${a.timeframe} Dollar Signal (Bull Only) ▲ BULLISH`,
    engulfing_consolidation_sell: (a) => `${a.timeframe} Engulfing SELL ▼`,
    triple_wick_rejection: (a) => `${a.timeframe} Triple Wick ${a.direction === "bullish" ? "▲ BULLISH" : "▼ BEARISH"}`,
    moving_average: (a) => `${a.timeframe} MA Cross ${a.direction === "bullish" ? "▲ GOLDEN" : "▼ DEATH"}`,
    engulfing_signal: (a) => `${a.timeframe} Engulfing ${a.direction === "bullish" ? "▲ BULLISH" : "▼ BEARISH"}`,
    engulfing_reversal: (a) => `${a.timeframe} Engulfing Reversal ${a.direction === "bullish" ? "▲ BULLISH" : "▼ BEARISH"}`,
    engulfing_consolidation: (a) => `${a.timeframe} Engulfing+Consolidation ${a.direction === "bullish" ? "▲ BULLISH" : "▼ BEARISH"}`,
    mark2: (a) => `${a.timeframe} Mark2 ${a.direction === "bullish" ? "▲ LONG" : "▼ SHORT"}`,
    mark_dollar_supertrend: (a) => `${a.timeframe} Mark Dollar ST ${a.direction === "bullish" ? "▲ LONG" : "▼ SHORT"}`,
    "20ema": (a) => `${a.timeframe} 20 EMA ${a.direction === "bullish" ? "▲ BUY" : "▼ SELL"}`,
    engulf_volume_fade: (a) => `${a.timeframe} Engulf Vol Fade ${a.direction === "bullish" ? "▲ BUY" : "▼ SELL"}`,
  };
  const TEST_ALERT_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4"];

  let alertsEnabled = false;
  let lastAlertKeys = new Set();
  let headerEl, alertOverlayEl, latestChangeEl, alertSoundEl, alertToggleBtn;

  function playAlertBuzz() {
    if (!alertSoundEl) return;
    try {
      alertSoundEl.currentTime = 0;
      const p = alertSoundEl.play();
      if (p && p.catch) p.catch((err) => console.warn("Alert sound blocked by browser:", err));
    } catch (err) {
      console.warn("Alert sound error:", err);
    }
  }

  function applyAlertEffect(alerts) {
    if (!alerts.length) return;
    playAlertBuzz();

    [headerEl, alertOverlayEl].forEach((el) => {
      if (!el) return;
      el.classList.remove("flash-bullish", "flash-bearish");
      void el.offsetWidth; // restart CSS animation
    });
    const hasBearish = alerts.some((a) => a.direction === "bearish");
    const cls = hasBearish ? "flash-bearish" : "flash-bullish";
    if (headerEl) headerEl.classList.add(cls);
    if (alertOverlayEl) alertOverlayEl.classList.add(cls);

    const latest = alerts[alerts.length - 1];
    const labelFn = ALERT_LABELS[latest.type] || ALERT_LABELS.supertrend_flip;
    if (latestChangeEl) {
      latestChangeEl.textContent = `${labelFn(latest)} @ ${new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false })}`;
      latestChangeEl.className = latest.direction;
    }
  }

  function checkAlerts(alerts) {
    const list = alerts || [];
    const currentKeys = new Set(list.map((a) => `${a.type}:${a.timeframe}:${a.direction}`));
    const newAlerts = list.filter((a) => !lastAlertKeys.has(`${a.type}:${a.timeframe}:${a.direction}`));
    lastAlertKeys = currentKeys;
    if (!alertsEnabled) return;
    applyAlertEffect(newAlerts);
  }

  function tickClocks() {
    const now = new Date();
    const ist = document.getElementById("clockIST");
    if (ist) ist.textContent = now.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false });
  }

  async function refreshPrice() {
    const el = document.getElementById("livePrice");
    if (!el) return;
    try {
      const res = await fetch("/api/price/");
      const data = await res.json();
      if (data.bid == null || data.ask == null) {
        el.textContent = "-- / --";
        return;
      }
      el.innerHTML = `<span class="bid">${data.bid}</span><span class="sep">/</span><span class="ask">${data.ask}</span>`;
    } catch (err) {
      el.textContent = "-- / --";
    }
  }

  function initAutoTrade() {
    const btn = document.getElementById("autoTradeToggleBtn");
    if (!btn) return;
    const csrfTokenEl = document.querySelector('meta[name="csrf-token"]');
    const csrfToken = csrfTokenEl ? csrfTokenEl.content : "";

    function setButton(enabled) {
      btn.textContent = `⚡ Auto Trade: ${enabled ? "ON" : "OFF"}`;
      btn.classList.toggle("live", enabled);
    }

    fetch("/api/auto-trade/").then((r) => r.json()).then((d) => setButton(d.enabled)).catch(() => {});

    btn.addEventListener("click", async () => {
      const aboutToEnable = !btn.classList.contains("live");
      if (aboutToEnable && !confirm(
        "Enable AUTO TRADING?\n\nThis will place REAL market orders the moment a candle closes with a " +
        "bull/bear signal from any ENABLED strategy, on every timeframe. Make sure lot size, stop-loss " +
        "and take-profit are configured the way you want before continuing."
      )) {
        return;
      }
      try {
        const res = await fetch("/api/auto-trade/toggle/", { method: "POST", headers: { "X-CSRFToken": csrfToken } });
        const data = await res.json();
        setButton(data.enabled);
      } catch (err) {}
    });
  }

  function init() {
    headerEl = document.querySelector("header");
    alertOverlayEl = document.getElementById("alertOverlay");
    latestChangeEl = document.getElementById("latestChange");
    alertSoundEl = document.getElementById("alertSound");
    alertToggleBtn = document.getElementById("alertToggleBtn");

    tickClocks();
    setInterval(tickClocks, 1000);
    refreshPrice();
    setInterval(refreshPrice, 1000);

    if (alertToggleBtn) {
      alertToggleBtn.addEventListener("click", () => {
        alertsEnabled = !alertsEnabled;
        alertToggleBtn.textContent = `🔔 Alerts: ${alertsEnabled ? "ON" : "OFF"}`;
        alertToggleBtn.classList.toggle("off", !alertsEnabled);
      });
    }

    const testBtn = document.getElementById("testAlertBtn");
    if (testBtn) {
      testBtn.addEventListener("click", () => {
        const timeframe = TEST_ALERT_TIMEFRAMES[Math.floor(Math.random() * TEST_ALERT_TIMEFRAMES.length)];
        const direction = Math.random() > 0.5 ? "bullish" : "bearish";
        const types = Object.keys(ALERT_LABELS);
        const type = types[Math.floor(Math.random() * types.length)];
        applyAlertEffect([{ timeframe, direction, type }]);
      });
    }

    initAutoTrade();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.AppHeader = { checkAlerts, applyAlertEffect };
})();
