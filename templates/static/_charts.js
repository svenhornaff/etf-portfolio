// ECharts bootstrap for the v3 report. docs/dev/report-v3-concept.md §4/§8/§9.
// Inlined verbatim into report.html.j2's final <script> — no module system,
// runs top to bottom once the DOM and #report-data are both present.
(function () {
  "use strict";
  var raw = document.getElementById("report-data").textContent;
  var data;
  try {
    data = JSON.parse(raw);
  } catch (parseError) {
    var errEl = document.createElement("p");
    errEl.style.color = "#c23b3b";
    errEl.style.padding = "1rem";
    errEl.textContent = "Fehler beim Laden der Berichtsdaten (" + parseError.message + ") — Diagramme können nicht angezeigt werden.";
    document.body.insertBefore(errEl, document.body.firstChild);
    return;
  }


  var COLOR_ACCENT = "#2457c5";
  var COLOR_POS = "#1a8a52";
  var COLOR_NEG = "#c23b3b";
  var COLOR_MUTED = "#9aa1ad";
  var PALETTE = ["#2457c5", "#1a8a52", "#b5790a", "#8a4fc2", "#c23b3b", "#0f7f8c", "#6b7280"];

  function fmtEurCompact(v) {
    var abs = Math.abs(v);
    var s;
    if (abs >= 1000) s = (v / 1000).toFixed(abs >= 10000 ? 0 : 1).replace(".", ",") + " T€";
    else s = v.toFixed(0) + " €";
    return s;
  }
  function fmtEurFull(v) {
    return v.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " €";
  }
  function fmtPct(v, d) {
    return (v * 100).toFixed(d == null ? 1 : d).replace(".", ",") + " %";
  }
  function el(id) { return document.getElementById(id); }
  function initChart(id) {
    var node = el(id);
    if (!node) return null;
    return echarts.init(node, null, { renderer: "svg" });
  }

  // ---------------------------------------------------------------- hero --
  (function heroChart() {
    var chart = initChart("chart-hero");
    if (!chart) return;
    var wealth = data.wealth || {};
    var valueSeries = wealth.value || [];
    var investedSeries = wealth.invested || [];
    var benchSeries = wealth.benchmark || [];
    var underwater = data.underwater || [];

    // Build an aligned date axis from the union of all series' dates, using
    // each series' own carry-forward (ECharts connectNulls handles gaps).
    function toMap(series) { var m = {}; series.forEach(function (p) { m[p[0]] = p[1]; }); return m; }
    var vMap = toMap(valueSeries), iMap = toMap(investedSeries), bMap = toMap(benchSeries);
    var dates = Object.keys(Object.assign({}, vMap, iMap, bMap)).sort();

    var value = dates.map(function (d) { return vMap[d] != null ? vMap[d] : null; });
    var invested = dates.map(function (d) { return iMap[d] != null ? iMap[d] : null; });
    var bench = dates.map(function (d) { return bMap[d] != null ? bMap[d] : null; });
    var gapPos = dates.map(function (_d, i) { return value[i] != null && invested[i] != null ? Math.max(0, value[i] - invested[i]) : null; });
    var gapNeg = dates.map(function (_d, i) { return value[i] != null && invested[i] != null ? Math.min(0, value[i] - invested[i]) : null; });
    var investedBase = invested; // stack base, drawn invisibly twice (bandUp/bandDown)

    var ddMap = toMap(underwater);
    var ddSeries = dates.map(function (d) { return ddMap[d] != null ? ddMap[d] : null; });

    var allTimeHighIdx = -1, peak = -Infinity;
    value.forEach(function (v, i) { if (v != null && v > peak) { peak = v; allTimeHighIdx = i; } });

    function eurOption() {
      return {
        animation: false,
        grid: { left: 54, right: 70, top: 20, bottom: 50 },
        tooltip: {
          trigger: "axis",
          formatter: function (params) {
            var i = params[0].dataIndex;
            var lines = [dates[i]];
            if (value[i] != null) lines.push("Depotwert: " + fmtEurFull(value[i]));
            if (invested[i] != null) lines.push("Einzahlungen: " + fmtEurFull(invested[i]));
            if (value[i] != null && invested[i] != null) lines.push("Gewinn: " + fmtEurFull(value[i] - invested[i]));
            if (bench[i] != null) lines.push("Benchmark: " + fmtEurFull(bench[i]));
            return lines.join("<br>");
          },
        },
        xAxis: { type: "category", data: dates, boundaryGap: false, axisLabel: { formatter: function (v) { return v.slice(0, 7); }, fontSize: 10 } },
        yAxis: { type: "value", min: 0, axisLabel: { formatter: fmtEurCompact, fontSize: 10 } },
        dataZoom: [{ type: "inside" }],
        series: [
          { name: "bandUpBase", type: "line", data: investedBase, stack: "bandUp", showSymbol: false, lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, silent: true, connectNulls: true },
          { name: "Gewinn", type: "line", data: gapPos, stack: "bandUp", showSymbol: false, lineStyle: { opacity: 0 }, areaStyle: { color: COLOR_POS, opacity: 0.18 }, connectNulls: true },
          { name: "bandDownBase", type: "line", data: investedBase, stack: "bandDown", showSymbol: false, lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, silent: true, connectNulls: true },
          { name: "Verlust", type: "line", data: gapNeg, stack: "bandDown", showSymbol: false, lineStyle: { opacity: 0 }, areaStyle: { color: COLOR_NEG, opacity: 0.18 }, connectNulls: true },
          { name: "Einzahlungen", type: "line", data: invested, showSymbol: false, lineStyle: { color: COLOR_MUTED, type: "dashed", width: 1.5 }, connectNulls: true },
          {
            name: "Depotwert", type: "line", data: value, showSymbol: false, lineStyle: { color: COLOR_ACCENT, width: 2 }, connectNulls: true,
            markPoint: allTimeHighIdx >= 0 ? { data: [{ coord: [dates[allTimeHighIdx], value[allTimeHighIdx]], name: "Hoch" }], symbolSize: 6, label: { formatter: "ATH", fontSize: 9, position: "top" }, itemStyle: { color: COLOR_ACCENT } } : undefined,
          },
          { name: data.wealth.benchmark_name || "Benchmark", type: "line", data: bench, showSymbol: false, lineStyle: { color: "#8a4fc2", type: "dashed", width: 1.5 }, connectNulls: true },
        ],
      };
    }

    function indexOption() {
      var base = value.find(function (v) { return v != null; }) || 1;
      var baseB = bench.find(function (v) { return v != null; }) || 1;
      var idxV = value.map(function (v) { return v != null ? (v / base) * 100 : null; });
      var idxB = bench.map(function (v) { return v != null ? (v / baseB) * 100 : null; });
      return {
        animation: false,
        grid: { left: 46, right: 20, top: 20, bottom: 50 },
        tooltip: { trigger: "axis", formatter: function (p) { var i = p[0].dataIndex; return dates[i] + "<br>Portfolio: " + (idxV[i] != null ? idxV[i].toFixed(1) : "n/a") + "<br>Benchmark: " + (idxB[i] != null ? idxB[i].toFixed(1) : "n/a"); } },
        xAxis: { type: "category", data: dates, boundaryGap: false, axisLabel: { formatter: function (v) { return v.slice(0, 7); }, fontSize: 10 } },
        yAxis: { type: "value", axisLabel: { fontSize: 10 } },
        dataZoom: [{ type: "inside" }],
        series: [
          { name: "Depotwert (=100)", type: "line", data: idxV, showSymbol: false, lineStyle: { color: COLOR_ACCENT, width: 2 }, connectNulls: true },
          { name: "Benchmark (=100)", type: "line", data: idxB, showSymbol: false, lineStyle: { color: "#8a4fc2", type: "dashed" }, connectNulls: true },
        ],
      };
    }

    function ddOption() {
      return {
        animation: false,
        grid: { left: 46, right: 20, top: 20, bottom: 50 },
        tooltip: { trigger: "axis", formatter: function (p) { var i = p[0].dataIndex; return dates[i] + "<br>Drawdown: " + (ddSeries[i] != null ? fmtPct(ddSeries[i]) : "n/a"); } },
        xAxis: { type: "category", data: dates, boundaryGap: false, axisLabel: { formatter: function (v) { return v.slice(0, 7); }, fontSize: 10 } },
        yAxis: { type: "value", max: 0, axisLabel: { formatter: function (v) { return (v * 100).toFixed(0) + "%"; }, fontSize: 10 } },
        dataZoom: [{ type: "inside" }],
        series: [{ name: "Drawdown", type: "line", data: ddSeries, showSymbol: false, areaStyle: { color: COLOR_NEG, opacity: 0.25 }, lineStyle: { color: COLOR_NEG }, connectNulls: true }],
      };
    }

    var optionsByMode = { eur: eurOption, index: indexOption, dd: ddOption };
    var currentMode = "eur";
    var currentRange = "max";

    function applyRange(opt) {
      if (currentRange === "max" || !dates.length) return opt;
      var lastDate = new Date(dates[dates.length - 1]);
      var fromDate;
      if (currentRange === "ytd") fromDate = new Date(lastDate.getFullYear(), 0, 1);
      else fromDate = new Date(lastDate.getTime() - currentRange * 86400000);
      var fromStr = fromDate.toISOString().slice(0, 10);
      var startIdx = dates.findIndex(function (d) { return d >= fromStr; });
      if (startIdx <= 0) return opt;
      opt.dataZoom = [{ type: "inside", startValue: dates[startIdx], endValue: dates[dates.length - 1] }];
      return opt;
    }

    function render() {
      chart.setOption(applyRange(optionsByMode[currentMode]()), true);
    }
    render();

    document.querySelectorAll("#hero-mode button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll("#hero-mode button").forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        currentMode = btn.dataset.mode;
        render();
      });
    });
    document.querySelectorAll("#hero-range button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll("#hero-range button").forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        currentRange = btn.dataset.range === "max" || btn.dataset.range === "ytd" ? btn.dataset.range : parseInt(btn.dataset.range, 10);
        render();
      });
    });
    window.addEventListener("resize", function () { chart.resize(); });
  })();

  // ------------------------------------------------------------ heatmap --
  (function monthlyHeatmap() {
    var chart = initChart("chart-heatmap");
    if (!chart) return;
    var months = data.monthly_returns || [];
    var monthsBench = data.monthly_returns_benchmark || [];
    var benchByMonth = {};
    monthsBench.forEach(function (m) { benchByMonth[m.month] = m.ret; });
    var years = [], seen = {};
    months.forEach(function (m) { var y = m.month.slice(0, 4); if (!seen[y]) { seen[y] = true; years.push(y); } });

    var rows = [];
    years.forEach(function (y) { rows.push(y); rows.push(y + " (Bench)"); });

    var cellsPortfolio = [];
    var cellsBench = [];
    months.forEach(function (m) {
      var y = m.month.slice(0, 4), mo = parseInt(m.month.slice(5, 7), 10) - 1;
      cellsPortfolio.push([mo, rows.indexOf(y), Math.round(m.ret * 1000) / 10]);
      var br = benchByMonth[m.month];
      if (br != null) cellsBench.push([mo, rows.indexOf(y + " (Bench)"), Math.round(br * 1000) / 10]);
    });
    var allVals = cellsPortfolio.concat(cellsBench).map(function (c) { return c[2]; });
    var maxAbs = Math.max(5, Math.max.apply(null, allVals.map(Math.abs)) || 5);

    chart.setOption({
      animation: false,
      tooltip: { position: "top", formatter: function (p) { return p.value[2] + " %"; } },
      grid: { left: 90, right: 10, top: 10, bottom: 30 },
      xAxis: { type: "category", data: ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"], splitArea: { show: true }, axisLabel: { fontSize: 10 } },
      yAxis: { type: "category", data: rows, splitArea: { show: true }, axisLabel: { fontSize: 10 } },
      visualMap: { min: -maxAbs, max: maxAbs, calculable: false, show: false, inRange: { color: ["#c23b3b", "#f4f5f7", "#1a8a52"] } },
      series: [{ type: "heatmap", data: cellsPortfolio.concat(cellsBench), label: { show: true, fontSize: 9, formatter: function (p) { return p.value[2]; } } }],
    });
    window.addEventListener("resize", function () { chart.resize(); });
  })();

  // -------------------------------------------------------- annual bars --
  (function annualBars() {
    var chart = initChart("chart-annual");
    if (!chart) return;
    var years = (data.annual_returns || []).map(function (r) { return r.year; });
    var port = (data.annual_returns || []).map(function (r) { return Math.round(r.ret * 1000) / 10; });
    var benchByYear = {};
    (data.annual_returns_benchmark || []).forEach(function (r) { benchByYear[r.year] = Math.round(r.ret * 1000) / 10; });
    var bench = years.map(function (y) { return benchByYear[y] != null ? benchByYear[y] : null; });
    chart.setOption({
      animation: false,
      tooltip: { trigger: "axis" },
      legend: { data: ["Portfolio", "Benchmark"], bottom: 0, textStyle: { fontSize: 10 } },
      grid: { left: 40, right: 10, top: 10, bottom: 40 },
      xAxis: { type: "category", data: years, axisLabel: { fontSize: 10 } },
      yAxis: { type: "value", axisLabel: { formatter: "{value} %", fontSize: 10 } },
      series: [
        { name: "Portfolio", type: "bar", data: port, itemStyle: { color: COLOR_ACCENT } },
        { name: "Benchmark", type: "bar", data: bench, itemStyle: { color: COLOR_MUTED } },
      ],
    });
    window.addEventListener("resize", function () { chart.resize(); });
  })();

  // ---------------------------------------------------------- contribution
  (function contributionChart() {
    var chart = initChart("chart-contribution");
    if (!chart) return;
    var all = Object.keys(data.realized_by_isin_named || {}).map(function (k) { return { name: k, value: data.realized_by_isin_named[k] }; });
    all.sort(function (a, b) { return b.value - a.value; });
    if (!all.length) { chart.setOption({ title: { text: "Noch keine realisierten Gewinne", left: "center", top: "middle", textStyle: { fontSize: 12, color: COLOR_MUTED } } }); return; }
    // docs/dev/report-v3-concept.md §3 Beitrag: top 5 + bottom 5 only, the rest
    // is in the collapsed Anhang bar-list (#realized-bars), not this chart.
    var entries = all.length > 10 ? all.slice(0, 5).concat(all.slice(-5)) : all;
    chart.setOption({
      animation: false,
      grid: { left: 140, right: 20, top: 10, bottom: 10 },
      tooltip: { formatter: function (p) { return p.name + ": " + fmtEurFull(p.value); } },
      xAxis: { type: "value", axisLabel: { formatter: fmtEurCompact, fontSize: 10 } },
      yAxis: { type: "category", data: entries.map(function (e) { return e.name; }).reverse(), axisLabel: { fontSize: 10 } },
      series: [{ type: "bar", data: entries.map(function (e) { return { value: e.value, itemStyle: { color: e.value >= 0 ? COLOR_POS : COLOR_NEG } }; }).reverse() }],
    });
    if (all.length > 10) {
      var note = document.createElement("p");
      note.className = "note";
      note.textContent = "Top 5 + Bottom 5 von " + all.length + " Instrumenten — alle in „Realisiert je Instrument“ im Anhang.";
      chart.getDom().insertAdjacentElement("afterend", note);
    }
    window.addEventListener("resize", function () { chart.resize(); });
  })();

  // -------------------------------------------------------------- waterfall
  (function waterfallChart() {
    var chart = initChart("chart-waterfall");
    if (!chart || !data.waterfall) return;
    var bars = data.waterfall.bars;
    var labels = bars.map(function (b) { return b.label; }).concat(["Depotwert"]);
    var base = bars.map(function (b) { return Math.min(b.start, b.start + b.delta); });
    base.push(0);
    var height = bars.map(function (b) { return Math.abs(b.delta); });
    height.push(data.waterfall.total);
    var colors = bars.map(function (b) { return b.delta >= 0 ? COLOR_POS : COLOR_NEG; });
    colors.push(COLOR_ACCENT);
    chart.setOption({
      animation: false,
      grid: { left: 60, right: 10, top: 10, bottom: 50 },
      tooltip: { formatter: function (p) { var i = p.dataIndex; return labels[i] + ": " + fmtEurFull(i < bars.length ? bars[i].delta : data.waterfall.total); } },
      xAxis: { type: "category", data: labels, axisLabel: { fontSize: 9, interval: 0, rotate: 20 } },
      yAxis: { type: "value", axisLabel: { formatter: fmtEurCompact, fontSize: 10 } },
      series: [
        { type: "bar", data: base, stack: "wf", itemStyle: { opacity: 0 }, silent: true },
        { type: "bar", data: height, stack: "wf", itemStyle: { color: function (p) { return colors[p.dataIndex]; } } },
      ],
    });
    window.addEventListener("resize", function () { chart.resize(); });
  })();

  // -------------------------------------------------------------- underwater
  (function underwaterChart() {
    var chart = initChart("chart-underwater");
    if (!chart) return;
    var uw = data.underwater || [];
    var uwBench = data.benchmark_underwater || [];
    if (!uw.length) { chart.setOption({ title: { text: "n/a — keine Kursabdeckung", left: "center", top: "middle", textStyle: { fontSize: 12, color: COLOR_MUTED } } }); return; }
    var bMap = {}; uwBench.forEach(function (p) { bMap[p[0]] = p[1]; });
    chart.setOption({
      animation: false,
      grid: { left: 46, right: 20, top: 10, bottom: 30 },
      tooltip: { trigger: "axis", formatter: function (p) { var d = p[0].axisValue; return d + "<br>Portfolio: " + fmtPct(p[0].data) + (p[1] ? "<br>Benchmark: " + fmtPct(p[1].data) : ""); } },
      xAxis: { type: "category", data: uw.map(function (p) { return p[0]; }), axisLabel: { formatter: function (v) { return v.slice(0, 7); }, fontSize: 10 } },
      yAxis: { type: "value", max: 0, axisLabel: { formatter: function (v) { return (v * 100).toFixed(0) + "%"; }, fontSize: 10 } },
      series: [
        { name: "Portfolio", type: "line", data: uw.map(function (p) { return p[1]; }), showSymbol: false, areaStyle: { color: COLOR_NEG, opacity: 0.2 }, lineStyle: { color: COLOR_NEG } },
        { name: "Benchmark", type: "line", data: uw.map(function (p) { return bMap[p[0]] != null ? bMap[p[0]] : null; }), showSymbol: false, lineStyle: { color: COLOR_MUTED, type: "dashed" }, connectNulls: true },
      ],
    });
    window.addEventListener("resize", function () { chart.resize(); });
  })();

  // ------------------------------------------------------------- allocation
  function pieChart(id, weights, names) {
    var chart = initChart(id);
    if (!chart) return;
    var entries = Object.keys(weights || {}).map(function (k) { return { name: (names && names[k]) || k, value: weights[k] }; });
    entries.sort(function (a, b) { return b.value - a.value; });
    chart.setOption({
      animation: false,
      tooltip: { formatter: function (p) { return p.name + ": " + fmtPct(p.value, 1); } },
      legend: { type: "scroll", bottom: 0, textStyle: { fontSize: 10 } },
      series: [{ type: "pie", radius: ["40%", "70%"], center: ["50%", "42%"], data: entries, color: PALETTE, label: { fontSize: 10, formatter: "{d}%" } }],
    });
    window.addEventListener("resize", function () { chart.resize(); });
  }
  pieChart("chart-alloc-position", data.holdings_weights, data.holdings_names);
  pieChart("chart-alloc-class", data.asset_class_weights, null);
  if (data.region_lt) pieChart("chart-region", data.region_lt, null);
  if (data.sector_lt) pieChart("chart-sector", data.sector_lt, null);
  if (data.currency_lt) pieChart("chart-currency", data.currency_lt, null);

  // -------------------------------------------------------- realized bars
  (function realizedBars() {
    var node = el("realized-bars");
    if (!node) return;
    var entries = Object.keys(data.realized_by_isin_named || {}).map(function (k) { return { name: k, value: data.realized_by_isin_named[k] }; });
    if (!entries.length) { node.innerHTML = '<p class="note">Noch keine realisierten Gewinne.</p>'; return; }
    entries.sort(function (a, b) { return b.value - a.value; });
    var maxAbs = Math.max.apply(null, entries.map(function (e) { return Math.abs(e.value); })) || 1;
    function rowNode(e) {
      var item = document.createElement("div");
      item.className = "item";
      var cls = e.value >= 0 ? "pos" : "neg";
      var pct = Math.min(100, (Math.abs(e.value) / maxAbs) * 50);

      var name = document.createElement("span");
      name.className = "name";
      name.title = e.name;
      name.textContent = e.name;

      var track = document.createElement("span");
      track.className = "bar-track";
      var fill = document.createElement("span");
      fill.className = "bar-fill " + cls;
      fill.style.width = pct + "%";
      track.appendChild(fill);

      var v = document.createElement("span");
      v.className = "v " + cls;
      v.textContent = fmtEurFull(e.value);

      item.appendChild(name);
      item.appendChild(track);
      item.appendChild(v);
      return item;
    }
    var top5 = entries.slice(0, 5), bottom5 = entries.length > 10 ? entries.slice(-5) : entries.slice(5);
    var rest = entries.length > 10 ? entries.slice(5, -5) : [];
    node.innerHTML = "";
    top5.forEach(function (e) { node.appendChild(rowNode(e)); });
    if (rest.length) {
      var details = document.createElement("details");
      var summary = document.createElement("summary");
      summary.style.cssText = "cursor:pointer;color:var(--muted);font-size:.8rem;padding:.3rem 0";
      summary.textContent = rest.length + " weitere …";
      details.appendChild(summary);
      rest.forEach(function (e) { details.appendChild(rowNode(e)); });
      node.appendChild(details);
    }
    bottom5.forEach(function (e) { node.appendChild(rowNode(e)); });
  })();

  // ------------------------------------------------------------ sell filter
  (function sellFilter() {
    var input = el("sell-filter");
    if (!input) return;
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      document.querySelectorAll("#sell-table tbody tr").forEach(function (tr) {
        var hay = tr.getAttribute("data-search") || "";
        tr.style.display = !q || hay.indexOf(q) !== -1 ? "" : "none";
      });
    });
  })();

  // ---------------------------------------------------------- hide amounts
  (function hideAmounts() {
    var btn = el("hide-toggle");
    if (!btn) return;
    btn.addEventListener("click", function () {
      document.body.classList.toggle("hidden-amounts");
      btn.textContent = document.body.classList.contains("hidden-amounts") ? "Beträge zeigen" : "Beträge verbergen";
    });
  })();

  // ------------------------------------------------------- goal simulator
  (function goalSimulator() {
    var els = {
      saving: el("g-saving"), years: el("g-years"), ret: el("g-return"), inflation: el("g-inflation"), wyears: el("g-wyears"),
    };
    if (!els.saving) return;
    var outs = {
      saving: el("g-saving-out"), years: el("g-years-out"), ret: el("g-return-out"), inflation: el("g-inflation-out"), wyears: el("g-wyears-out"),
    };
    var startValue = Number((data.start_value != null ? data.start_value : 0));
    var fanChart = initChart("chart-fan");

    function futureValue(start, monthly, annualReturn, years) {
      var monthlyRate = Math.pow(1 + annualReturn, 1 / 12) - 1;
      var months = years * 12;
      var fv = start * Math.pow(1 + monthlyRate, months);
      for (var m = 1; m <= months; m++) fv += monthly * Math.pow(1 + monthlyRate, months - m);
      return fv;
    }
    function series(start, monthly, annualReturn, years) {
      var monthlyRate = Math.pow(1 + annualReturn, 1 / 12) - 1;
      var pts = [];
      var v = start;
      for (var m = 0; m <= years * 12; m++) {
        if (m > 0) v = v * (1 + monthlyRate) + monthly;
        if (m % 12 === 0) pts.push(Math.round(v));
      }
      return pts;
    }

    function recompute() {
      var monthly = Number(els.saving.value);
      var years = Number(els.years.value);
      var ret = Number(els.ret.value) / 100;
      var inflation = Number(els.inflation.value) / 100;
      var wyears = Number(els.wyears.value);

      outs.saving.textContent = monthly.toLocaleString("de-DE");
      outs.years.textContent = years;
      outs.ret.textContent = Number(els.ret.value).toLocaleString("de-DE") + " %";
      outs.inflation.textContent = Number(els.inflation.value).toLocaleString("de-DE") + " %";
      outs.wyears.textContent = wyears;

      var fvNominal = futureValue(startValue, monthly, ret, years);
      var fvReal = fvNominal / Math.pow(1 + inflation, years);
      var monthlyRateW = Math.pow(1 + ret, 1 / 12) - 1;
      var monthsW = wyears * 12;
      var withdrawal = monthlyRateW > 0 ? (fvNominal * monthlyRateW) / (1 - Math.pow(1 + monthlyRateW, -monthsW)) : fvNominal / monthsW;

      el("g-fv-nominal").textContent = fmtEurFull(fvNominal);
      el("g-fv-real").textContent = fmtEurFull(fvReal);
      el("g-withdrawal").textContent = fmtEurFull(withdrawal) + " / Monat";

      var progressEl = el("g-progress");
      if (progressEl && data.target_wealth_10y) {
        var pct10 = (futureValue(startValue, monthly, ret, 10) / data.target_wealth_10y) * 100;
        progressEl.textContent = "Fortschritt zum Ziel (10 J, " + fmtEurFull(data.target_wealth_10y) + "): " + pct10.toFixed(0) + " %";
      }

      if (fanChart) {
        var years4 = series(startValue, monthly, 0.04, years);
        var years7 = series(startValue, monthly, 0.07, years);
        var years10 = series(startValue, monthly, 0.10, years);
        var xs = years4.map(function (_, i) { return "Jahr " + i; });
        fanChart.setOption({
          animation: false,
          grid: { left: 54, right: 10, top: 10, bottom: 30 },
          legend: { data: ["4 %", "7 %", "10 %"], bottom: 0, textStyle: { fontSize: 10 } },
          tooltip: { trigger: "axis", valueFormatter: fmtEurFull },
          xAxis: { type: "category", data: xs, axisLabel: { fontSize: 9, interval: Math.ceil(xs.length / 10) } },
          yAxis: { type: "value", axisLabel: { formatter: fmtEurCompact, fontSize: 10 } },
          series: [
            { name: "4 %", type: "line", data: years4, showSymbol: false, lineStyle: { color: COLOR_NEG } },
            { name: "7 %", type: "line", data: years7, showSymbol: false, lineStyle: { color: COLOR_ACCENT } },
            { name: "10 %", type: "line", data: years10, showSymbol: false, lineStyle: { color: COLOR_POS } },
          ],
        });
      }
    }

    document.querySelectorAll("#g-return-presets button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll("#g-return-presets button").forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        els.ret.value = btn.dataset.v;
        recompute();
      });
    });
    Object.keys(els).forEach(function (k) { els[k].addEventListener("input", recompute); });
    recompute(); // §10/§11: compute on load, not just on first interaction (v2 bug)
    window.addEventListener("resize", function () { if (fanChart) fanChart.resize(); });
  })();
})();
