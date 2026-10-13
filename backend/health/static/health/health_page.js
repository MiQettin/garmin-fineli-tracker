(function () {
  "use strict";

  // Texts in the app language, provided by health_page.html (English fallback).
  var T = Object.assign({
    loading: "Loading…",
    requestFailed: "Request failed ({status})",
    couldNotLoad: "Could not load health data: {error}",
    couldNotLoadTrends: "Could not load trend charts: {error}",
    noData: "No data",
    current: "Current:",
    vs28: "vs 28d avg",
    avg28: "28d avg:",
    samples: "7d: {n7} samples · 28d: {n28} samples · 90d: {n90} samples",
    fromBeforeTo: "From date must be on or before To date.",
    hrvLabel: "HRV (ms)",
    rhrLabel: "Resting HR (bpm)",
  }, window.HEALTH_I18N || {});

  var scriptTag = document.currentScript;
  var analyticsUrl = scriptTag.getAttribute("data-analytics-url");
  var dateInput = document.getElementById("health-date");
  var statusEl = document.getElementById("health-status");

  var rangeFromInput = document.getElementById("range-from");
  var rangeToInput = document.getElementById("range-to");
  var rangeValidationEl = document.getElementById("range-validation");
  var rangeStatusEl = document.getElementById("range-status");
  var hrvChart = null;
  var rhrChart = null;

  function todayISO() {
    var now = new Date();
    var local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 10);
  }

  function daysAgoISO(days) {
    var now = new Date();
    var local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
    local.setDate(local.getDate() - days);
    return local.toISOString().slice(0, 10);
  }

  function fmt(value, unit) {
    if (value === null || value === undefined) return "—";
    var display = typeof value === "number" ? Math.round(value) : value;
    return unit ? display + " " + unit : String(display);
  }

  function fmtHours(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    var totalMinutes = Math.round(seconds / 60);
    var h = Math.floor(totalMinutes / 60);
    var m = totalMinutes % 60;
    return h + " h " + (m < 10 ? "0" + m : m) + " min";
  }

  function fmtDelta(value, unit) {
    if (value === null || value === undefined) return "—";
    var rounded = Math.round(value);
    var sign = rounded > 0 ? "+" : "";
    return sign + rounded + (unit ? " " + unit : "") + " " + T.vs28;
  }

  function fmtDeltaHours(value) {
    if (value === null || value === undefined) return "—";
    var sign = value > 0 ? "+" : "";
    return sign + (value / 3600).toFixed(1) + " h " + T.vs28;
  }

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function renderOverviewCard(prefix, metric, unit, isDuration) {
    if (!metric) return;
    var format = isDuration ? fmtHours : function (v) { return fmt(v, unit); };
    setText(prefix + "-value", format(metric.current));
    setText(prefix + "-sub", T.avg28 + " " + format(metric.avg_28d));
  }

  function renderTrendCard(prefix, metric, unit, isDuration) {
    if (!metric) return;
    var format = isDuration ? fmtHours : function (v) { return fmt(v, unit); };
    setText(prefix + "-current", format(metric.current));
    setText(prefix + "-current-date", metric.current_date || "");
    setText(prefix + "-avg7", format(metric.avg_7d));
    setText(prefix + "-avg28", format(metric.avg_28d));
    setText(prefix + "-avg90", format(metric.avg_90d));
    setText(
      prefix + "-delta",
      isDuration ? fmtDeltaHours(metric.delta_vs_28d) : fmtDelta(metric.delta_vs_28d, unit)
    );
    var c7 = metric.sample_count_7d != null ? metric.sample_count_7d : 0;
    var c28 = metric.sample_count_28d != null ? metric.sample_count_28d : 0;
    var c90 = metric.sample_count_90d != null ? metric.sample_count_90d : 0;
    setText(prefix + "-counts", T.samples.replace("{n7}", c7).replace("{n28}", c28).replace("{n90}", c90));
  }

  function render(a) {
    renderOverviewCard("ov-hrv", a.hrv, "ms", false);
    renderOverviewCard("ov-rhr", a.resting_hr, "bpm", false);
    renderOverviewCard("ov-stress", a.stress, null, false);
    renderOverviewCard("ov-sleep-duration", a.sleep.duration, null, true);
    renderOverviewCard("ov-sleep-score", a.sleep.sleep_score, null, false);
    renderOverviewCard("ov-bb-end", a.body_battery.end, null, false);
    renderOverviewCard("ov-steps", a.activity.steps, null, false);

    renderTrendCard("trend-hrv", a.hrv, "ms", false);
    renderTrendCard("trend-rhr", a.resting_hr, "bpm", false);
    renderTrendCard("trend-stress", a.stress, null, false);
    renderTrendCard("trend-sleep-duration", a.sleep.duration, null, true);
    renderTrendCard("trend-sleep-score", a.sleep.sleep_score, null, false);

    renderTrendCard("bb-start", a.body_battery.start, null, false);
    renderTrendCard("bb-end", a.body_battery.end, null, false);
    renderTrendCard("bb-charged", a.body_battery.charged, null, false);
    renderTrendCard("bb-drained", a.body_battery.drained, null, false);

    renderTrendCard("sleep-duration", a.sleep.duration, null, true);
    renderTrendCard("sleep-score", a.sleep.sleep_score, null, false);
    renderTrendCard("sleep-hr", a.sleep.avg_heart_rate, "bpm", false);
    renderTrendCard("sleep-stress", a.sleep.avg_stress, null, false);

    renderTrendCard("resp-waking", a.respiration.waking, "br/min", false);
    renderTrendCard("resp-sleep", a.respiration.sleep, "br/min", false);
    renderTrendCard("spo2-daily", a.spo2.daily, "%", false);
    renderTrendCard("spo2-sleep", a.spo2.sleep, "%", false);

    renderTrendCard("steps", a.activity.steps, null, false);
    renderTrendCard("mod-min", a.activity.moderate_intensity_minutes, "min", false);
    renderTrendCard("vig-min", a.activity.vigorous_intensity_minutes, "min", false);
  }

  function loadAnalytics(endDate) {
    statusEl.hidden = false;
    statusEl.classList.remove("is-error");
    statusEl.textContent = T.loading;

    fetch(analyticsUrl + "?end_date=" + encodeURIComponent(endDate))
      .then(function (response) {
        if (!response.ok) {
          return response.json().catch(function () { return {}; }).then(function (body) {
            throw new Error(body.error || T.requestFailed.replace("{status}", response.status));
          });
        }
        return response.json();
      })
      .then(function (data) {
        render(data.analytics);
        statusEl.hidden = true;
      })
      .catch(function (err) {
        statusEl.classList.add("is-error");
        statusEl.textContent = T.couldNotLoad.replace("{error}", err.message);
        statusEl.hidden = false;
      });
  }

  function buildChart(canvasId, label, series, existingChart, color) {
    if (existingChart) existingChart.destroy();
    var ctx = document.getElementById(canvasId).getContext("2d");
    return new Chart(ctx, {
      type: "line",
      data: {
        labels: series.map(function (point) { return point.date; }),
        datasets: [{
          label: label,
          data: series.map(function (point) { return point.value; }),
          spanGaps: false,
          borderColor: color,
          backgroundColor: color,
          pointRadius: 2,
          pointHoverRadius: 4,
          tension: 0.25,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                return ctx.parsed.y === null ? T.noData : label + ": " + ctx.parsed.y;
              },
            },
          },
        },
        scales: {
          x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10 } },
          y: { beginAtZero: false },
        },
      },
    });
  }

  function renderRangeCharts(a) {
    var hrvSeries = a.hrv.daily_series || [];
    var rhrSeries = a.resting_hr.daily_series || [];

    hrvChart = buildChart("hrv-chart", T.hrvLabel, hrvSeries, hrvChart, "#2563eb");
    rhrChart = buildChart("rhr-chart", T.rhrLabel, rhrSeries, rhrChart, "#7c3aed");

    var hrvHasData = hrvSeries.some(function (p) { return p.value !== null; });
    var rhrHasData = rhrSeries.some(function (p) { return p.value !== null; });
    var hrvEmptyEl = document.getElementById("hrv-chart-empty");
    var rhrEmptyEl = document.getElementById("rhr-chart-empty");
    if (hrvEmptyEl) hrvEmptyEl.hidden = hrvHasData;
    if (rhrEmptyEl) rhrEmptyEl.hidden = rhrHasData;

    setText(
      "hrv-chart-now",
      T.current + " " + fmt(a.hrv.current, "ms") + (a.hrv.current_date ? " (" + a.hrv.current_date + ")" : "")
    );
    setText(
      "rhr-chart-now",
      T.current + " " + fmt(a.resting_hr.current, "bpm") + (a.resting_hr.current_date ? " (" + a.resting_hr.current_date + ")" : "")
    );
  }

  function loadRange() {
    var from = rangeFromInput.value;
    var to = rangeToInput.value;

    if (!from || !to) return;

    // Explicit From/To validation - an invalid range must never reach fetch(),
    // and dates are never silently swapped.
    if (from > to) {
      rangeValidationEl.hidden = false;
      rangeValidationEl.textContent = T.fromBeforeTo;
      return;
    }
    rangeValidationEl.hidden = true;
    rangeValidationEl.textContent = "";

    rangeStatusEl.hidden = false;
    rangeStatusEl.classList.remove("is-error");
    rangeStatusEl.textContent = T.loading;

    fetch(analyticsUrl + "?start_date=" + encodeURIComponent(from) + "&end_date=" + encodeURIComponent(to))
      .then(function (response) {
        if (!response.ok) {
          return response.json().catch(function () { return {}; }).then(function (body) {
            throw new Error(body.error || T.requestFailed.replace("{status}", response.status));
          });
        }
        return response.json();
      })
      .then(function (data) {
        renderRangeCharts(data.analytics);
        rangeStatusEl.hidden = true;
      })
      .catch(function (err) {
        rangeStatusEl.classList.add("is-error");
        rangeStatusEl.textContent = T.couldNotLoadTrends.replace("{error}", err.message);
        rangeStatusEl.hidden = false;
      });
  }

  dateInput.value = todayISO();
  dateInput.addEventListener("change", function () {
    loadAnalytics(dateInput.value);
  });
  loadAnalytics(dateInput.value);

  rangeFromInput.value = daysAgoISO(27);
  rangeToInput.value = todayISO();
  rangeFromInput.addEventListener("change", loadRange);
  rangeToInput.addEventListener("change", loadRange);
  loadRange();
})();
