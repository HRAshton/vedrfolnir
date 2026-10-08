// Optional DOM integration check. Requires jsdom and the default 8-day synthetic
// monitoring_smoke fixture served by Uvicorn. It does not validate browser layout.
const { JSDOM, VirtualConsole } = require("jsdom");
const fs = require("fs"),
  path = require("node:path"),
  assert = require("node:assert/strict");
(async () => {
  const errors = [],
    base = process.env.DASHBOARD_URL || "http://127.0.0.1:8080";
  let fail = false,
    empty = false,
    stale = false,
    interval;
  for (let i = 0; i < 100; i++) {
    try {
      await fetch(base + "/health");
      break;
    } catch (e) {
      await new Promise((r) => setTimeout(r, 50));
    }
  }
  const html = fs.readFileSync(
    path.resolve(__dirname, "../src/vedrfolnir/static/dashboard.html"),
    "utf8",
  );
  const console = new VirtualConsole();
  console.on("jsdomError", (e) => errors.push(e.message));
  const dom = new JSDOM(html, {
    url: base,
    runScripts: "dangerously",
    pretendToBeVisual: true,
    virtualConsole: console,
    beforeParse(w) {
      w.AbortController = AbortController;
      w.fetch = async (url, opts) => {
        if (fail && url === "/api/job-metrics")
          return { ok: false, status: 503 };
        const response = await fetch(base + url, opts);
        const data = await response.json();
        if (empty && url.startsWith("/api/job-metrics/by-workload"))
          data.cohorts = [];
        if (empty && url.startsWith("/api/job-metrics/daily"))
          for (const r of data.rows)
            for (const s of ["queued", "running"])
              for (const f of ["wait_error", "finish_error"])
                r[s][f] = {
                  count: 0,
                  mae_minutes: null,
                  p90_absolute_error_minutes: null,
                  signed_bias_minutes: null,
                };
        if (stale && url.startsWith("/api/jobs?")) {
          data.data_source_sync = "stale";
          for (const j of data.jobs) {
            j.prediction.forecast_available = false;
            j.prediction.unavailable_reasons = ["source_stale"];
            j.prediction.predicted_start_at = null;
            j.prediction.predicted_finish_at = null;
            j.prediction.predicted_wait_minutes = null;
          }
        }
        return {
          ok: response.ok,
          status: response.status,
          json: async () => data,
        };
      };
      w.setInterval = (fn, ms) => {
        assert.equal(ms, 15000);
        interval = fn;
        return 1;
      };
      w.HTMLDialogElement.prototype.showModal = function () {
        this.open = true;
      };
      w.HTMLDialogElement.prototype.close = function () {
        this.open = false;
      };
    },
  });
  const w = dom.window,
    d = w.document,
    wait = async (fn) => {
      for (let i = 0; i < 100; i++) {
        if (fn()) return;
        await new Promise((r) => setTimeout(r, 20));
      }
      throw Error("Condition timed out");
    };
  await wait(() =>
    d.getElementById("view-time").textContent.startsWith("View updated"),
  );
  assert.equal(d.getElementById("health-label").textContent, "Source recent");
  assert.equal(d.querySelectorAll("#jobs tr").length, 2);
  assert.equal(d.querySelectorAll("#outcomes tr").length, 12);
  assert(d.querySelector("#chart svg"));
  assert.equal(d.querySelectorAll("#job-cohorts tr").length, 1);
  assert(d.getElementById("job-cohorts").textContent.includes("builders"));
  assert(d.getElementById("job-cohorts").textContent.includes("Small sample"));
  d.getElementById("group-by").value = "job_type";
  d.getElementById("group-by").dispatchEvent(new w.Event("change"));
  await wait(
    () => d.getElementById("group-heading").textContent === "Job type",
  );
  assert(d.getElementById("job-cohorts").textContent.includes("package"));
  d.querySelector("#jobs button").click();
  await wait(() =>
    d.getElementById("detail-body").textContent.includes("Finish error"),
  );
  assert(d.getElementById("detail").open);
  d.getElementById("close-detail").click();
  assert(!d.getElementById("detail").open);
  d.getElementById("target").value = "running.finish_error";
  d.getElementById("target").dispatchEvent(new w.Event("change"));
  assert(
    d
      .getElementById("chart-note")
      .textContent.includes("16 scored observations"),
  );
  assert(d.getElementById("job-cohorts").textContent.includes("compile"));
  assert(d.getElementById("job-cohorts").textContent.includes("package"));
  d.getElementById("group-by").value = "load";
  d.getElementById("group-by").dispatchEvent(new w.Event("change"));
  await wait(
    () => d.getElementById("group-heading").textContent === "Pool load",
  );
  assert(d.getElementById("job-cohorts").textContent.includes("busy"));
  assert(d.getElementById("job-cohorts").textContent.includes("idle"));
  d.getElementById("days").value = "7";
  d.getElementById("days").dispatchEvent(new w.Event("change"));
  await wait(() =>
    d.getElementById("chart-note").textContent.includes("7 days"),
  );
  empty = true;
  interval();
  await wait(() =>
    d.getElementById("chart").textContent.includes("No completed forecasts"),
  );
  assert(!d.querySelector("#chart svg"));
  assert(
    d
      .getElementById("job-cohorts")
      .textContent.includes("No completed observations"),
  );
  empty = false;
  stale = true;
  interval();
  await wait(
    () => d.getElementById("health-label").textContent === "Source stale",
  );
  assert(d.getElementById("jobs").textContent.includes("source stale"));
  stale = false;
  fail = true;
  d.getElementById("refresh").click();
  await wait(() => !d.getElementById("error").hidden);
  assert.equal(d.getElementById("health-label").textContent, "View stale");
  assert(d.getElementById("error").textContent.includes("503"));
  fail = false;
  d.getElementById("refresh").click();
  await wait(() => d.getElementById("error").hidden);
  assert.equal(d.getElementById("health-label").textContent, "Source recent");
  assert.deepEqual(errors, []);
  process.stdout.write(
    "Dashboard DOM/live-API checks passed: rendering, pool/job/load breakdowns, target isolation, dialog, chart windows, empty state, auto refresh, stale source, failed refresh and recovery.\n",
  );
  dom.window.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
