"""Offline benchmark report, using only stdlib and the recorded forecasts."""
from __future__ import annotations

from collections import defaultdict
from html import escape
from pathlib import Path
import statistics

from .features import quantile


def _num(value, digits=1):
    return ' - ' if value is None else f'{value:,.{digits}f}'


def _bar(value, maximum, *, mode='baseline'):
    width = min(100, 100 * value / max(.001, maximum))
    return f'<div class="bar-track"><div class="bar {mode}" style="width:{width:.2f}%"></div></div>'


def _scores(rows, field):
    if not rows:
        return None
    errors = [r[field] - r['actual_minutes'] for r in rows]
    abs_errors = [abs(e) for e in errors]
    return dict(count=len(rows), mae=statistics.mean(abs_errors),
                p50=quantile(abs_errors, .5), p90=quantile(abs_errors, .9),
                bias=statistics.mean(errors))


def write_report(result, destination: Path):
    """Build a self-contained HTML report; no CDN, JavaScript or remote assets."""
    s = result['summary']
    forecasts = result['forecasts']
    first = {}
    for row in forecasts:
        first.setdefault(row['plan_id'], row)
    scored = set(result['scored_plan_ids'])
    rows = [r for r in first.values() if r['plan_id'] in scored]
    b, m = _scores(rows, 'baseline_minutes'), _scores(rows, 'predicted_minutes')
    delta = (100 * (m['mae'] - b['mae']) / b['mae']) if b and m and b['mae'] else None
    caption = ('River improved plan-start MAE' if delta is not None and delta < 0 else
               'River did not improve plan-start MAE' if delta is not None and s['river_used_plans'] else
               'River has not yet been used; selected predictor is the baseline')
    grade = 'good' if delta is not None and delta < 0 else 'warn'

    cards = [
        ('Plans evaluated', str(s['plans_evaluated']), f"{s['plans_total']} total"),
        ('Baseline MAE', f"{_num(b['mae']) if b else ' - '} min", 'At plan start'),
        ('Selected MAE', f"{_num(m['mae']) if m else ' - '} min", 'At plan start'),
        ('River-selected plans', str(s['river_used_plans']), 'Rest use baseline'),
        ('River shadow-ready', str(s.get('river_shadow_ready_plans', 0)), 'Qualified for paired validation'),
    ]
    cards_html = ''.join(
        f'<div class="metric"><div class="muted">{escape(k)}</div><div class="big">{escape(v)}</div><small>{escape(note)}</small></div>'
        for k, v, note in cards)

    comparison = ''
    if b and m:
        metrics = [('MAE', b['mae'], m['mae']), ('Median absolute error', b['p50'], m['p50']),
                   ('P90 absolute error', b['p90'], m['p90']), ('Signed bias', b['bias'], m['bias'])]
        comp_rows = ''.join(
            f'<tr><td>{escape(label)}</td><td>{_num(a, 2)}</td><td>{_num(x, 2)}</td></tr>'
            for label, a, x in metrics)
        comparison = f'<table><thead><tr><th>Metric (minutes)</th><th>Baseline</th><th>Selected predictor</th></tr></thead><tbody>{comp_rows}</tbody></table>'

    buckets = defaultdict(list)
    for f in forecasts:
        buckets[f['phase']].append(f)
    phases = ['start', 'quarter', 'half', 'late']
    ph = [(name, _scores(buckets[name], 'baseline_minutes'), _scores(buckets[name], 'predicted_minutes'))
          for name in phases if buckets[name]]
    max_ph = max([max(b['mae'], m['mae']) for _, b, m in ph] or [1.0])
    phase_html = ''.join(
        f'<tr><td>{name}</td><td>{b["count"]}</td><td>{_num(b["mae"])}{_bar(b["mae"],max_ph)}</td>'
        f'<td>{_num(m["mae"])}{_bar(m["mae"],max_ph,mode="model")}</td></tr>'
        for name, b, m in ph)

    worst = sorted(rows, key=lambda r: abs(r['predicted_minutes'] - r['actual_minutes']), reverse=True)[:20]
    worst_html = ''.join(
        f'<tr><td class="id">{escape(str(r["plan_id"]))}</td><td>{_num(r["actual_minutes"])}</td>'
        f'<td>{_num(r["baseline_minutes"])}</td><td>{_num(r["predicted_minutes"])}</td>'
        f'<td>{_num(r["predicted_minutes"] - r["actual_minutes"])}</td>'
        f'<td>{"River" if r["river_used"] else "Baseline"}</td></tr>'
        for r in worst)

    # Real workflow family breakdown; all entries are start-time forecasts.
    grouped = defaultdict(list)
    for row in rows:
        if row.get('family'):
            grouped[row['family']].append(row)
    family_html = ''
    if grouped:
        family_rows = []
        for family, family_records in sorted(grouped.items(), key=lambda x: (-len(x[1]), x[0]))[:40]:
            base = _scores(family_records, 'baseline_minutes')
            chosen = _scores(family_records, 'predicted_minutes')
            family_rows.append(f'<tr><td>{escape(str(family))}</td><td>{len(family_records)}</td>'
                               f'<td>{_num(base["mae"])}</td><td>{_num(chosen["mae"])}</td></tr>')
        family_html = ('<div class="panel"><h2>Accuracy by workflow family / repository</h2><table>'
                       '<thead><tr><th>Family</th><th>Workflows</th><th>Baseline MAE (min)</th>'
                       '<th>Selected MAE (min)</th></tr></thead><tbody>'
                       + ''.join(family_rows) + '</tbody></table></div>')

    # Histogram computed on exactly the scored plan-start snapshots.
    edges = [0, 1, 5, 15, 30, 60, 120, float('inf')]
    labels = ['<1', '1-5', '5-15', '15-30', '30-60', '60-120', '120+']
    hist = []
    for a, z, label in zip(edges, edges[1:], labels):
        bv = sum(a <= abs(r['baseline_minutes'] - r['actual_minutes']) < z for r in rows)
        mv = sum(a <= abs(r['predicted_minutes'] - r['actual_minutes']) < z for r in rows)
        hist.append((label, bv, mv))
    max_hist = max([max(x, y) for _, x, y in hist] or [1])
    hist_html = ''.join(
        f'<tr><td>{label} min</td><td>{bv}{_bar(bv, max_hist)}</td><td>{mv}{_bar(mv, max_hist,mode="model")}</td></tr>'
        for label, bv, mv in hist)

    diagnostics = s.get("dataset_stats") or {}
    job_html = ''
    if "job_metrics" in s:
        job_rows = ''.join(
            f'<tr><td>{escape(status)}</td><td>{m["jobs_observed"]}</td>'
            f'<td>{m["forecast_available_jobs"]}</td>'
            f'<td>{_num(m.get("wait_error", {}).get("mae_minutes"))}</td>'
            f'<td>{_num(m["finish_error"]["mae_minutes"])}</td></tr>'
            for status, m in s["job_metrics"].items())
        job_html = ('<div class="panel"><h2>Individual job forecasts</h2>'
                    '<p class="muted">First observation per job and status, after plan warmup. '
                    'Jobs within a workflow are correlated; these are separate targets from plan ETA. '
                    'No job-level P90 coverage is claimed.</p>'
                    '<a href="job_predictions.csv">Open job_predictions.csv</a><table>'
                    '<thead><tr><th>Status</th><th>Jobs observed</th><th>Available</th>'
                    '<th>Wait MAE (min)</th><th>Finish MAE (min)</th></tr></thead><tbody>'
                    + job_rows + '</tbody></table></div>')
    scheduler_html = ''
    if "scheduler_fallback_reason_counts" in s:
        counts = s["scheduler_fallback_reason_counts"]
        scheduler_html = (
            '<div class="panel"><h2>Scheduler eligibility (scored first forecasts)</h2>'
            f'<p>{s["scheduler_eligible_plans"]} eligible; {s["fallback_plans"]} fallback.</p>'
            '<p class="muted">Blockers can overlap; recorded admission does not mean every job is runnable yet.</p>'
            '<table><thead><tr><th>Fallback reason</th><th>Plans</th></tr></thead><tbody>'
            + ''.join(f'<tr><td>{escape(reason)}</td><td>{count}</td></tr>'
                      for reason, count in sorted(counts.items()))
            + '</tbody></table></div>'
        )
    diagnostics_html = (
        '<div class="panel"><h2>Dataset import</h2><table><tbody>'
        + ''.join(f'<tr><td>{escape(str(k).replace("_", " "))}</td><td>{escape(str(v))}</td></tr>'
                  for k, v in diagnostics.items())
        + '</tbody></table></div>' if diagnostics else ''
    )
    runtime = (f"Data load: {s['data_load_seconds']:.1f}s · Evaluation: {s['evaluation_seconds']:.1f}s"
               if 'data_load_seconds' in s and 'evaluation_seconds' in s else 'Runtime not recorded')
    source = escape(str(s.get('source', 'unknown')))
    scope = escape(str(s.get('benchmark_scope', 'unknown')))
    queue = escape(str(s.get('queue_observability', 'unavailable')))
    change = f'{delta:+.1f}%' if delta is not None and s['river_used_plans'] else 'not yet comparable'
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vedrfolnir benchmark</title><style>
:root{{--bg:#f6f7fb;--panel:white;--text:#172033;--muted:#667085;--b:#778396;--m:#2563eb}}
*{{box-sizing:border-box}}body{{font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--text);margin:0}}
main{{max-width:1080px;margin:28px auto;padding:0 18px 80px}}header{{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap}}
h1{{margin-bottom:2px;font-size:30px}}h2{{font-size:19px}}p{{margin-top:4px}}.muted,small{{color:var(--muted)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:22px 0}}.metric,.panel{{padding:18px;background:var(--panel);border:1px solid #e5e7eb;border-radius:12px}}
.big{{font-size:27px;font-weight:720;margin:4px 0}}.panel{{margin:15px 0;overflow:auto}}table{{width:100%;border-collapse:collapse;text-align:left}}
th,td{{padding:10px;border-bottom:1px solid #e5e7eb;vertical-align:middle}}th{{font-size:13px;color:var(--muted)}}
.bar-track{{margin-top:3px;background:#e7ebf0;height:7px;min-width:70px;border-radius:10px;overflow:hidden}}.bar{{height:100%;background:var(--b)}}.bar.model{{background:var(--m)}}
.id{{font:12px ui-monospace,monospace;overflow-wrap:anywhere;max-width:340px}}.chip{{padding:6px 11px;border-radius:9px;background:#eef2ff}}.chip.good{{background:#e8f8ed}}.chip.warn{{background:#fff5df}}
code{{overflow-wrap:anywhere}}a{{color:#1d4ed8}}.note{{background:#f0f5ff;padding:12px;border-radius:8px}}
@media(prefers-color-scheme:dark){{:root{{--bg:#10141e;--panel:#1b2130;--text:#f1f5ff;--muted:#b4bed0;--b:#768499;--m:#6eaeff}}.metric,.panel{{border-color:#343d52}}th,td{{border-color:#343d52}}.bar-track{{background:#384154}}.chip{{background:#293450}}.chip.good{{background:#1b4930}}.chip.warn{{background:#563b1a}}.note{{background:#293450}}a{{color:#a5c8ff}}}}
</style></head><body><main><header><div><h1>Vedrfolnir benchmark</h1><p class="muted">Offline chronological ETA evaluation · {scope} · {escape(runtime)}</p></div><a href="predictions.csv">Open predictions.csv</a></header>
<div class="cards">{cards_html}</div><div class="panel"><strong class="chip {grade}">{escape(caption)} · {change}</strong>
<p class="muted" style="margin-top:14px">Comparison below uses the same scored plan-start forecasts. A lower error is better; positive bias means predicted too late.</p>{comparison}</div>
{family_html}{diagnostics_html}{scheduler_html}{job_html}<div class="panel"><h2>Absolute error distribution (scored plan starts)</h2><table><thead><tr><th>Error</th><th>Baseline count</th><th>Selected count</th></tr></thead><tbody>{hist_html}</tbody></table></div>
<div class="panel"><h2>{"Plan-start only (no progress timestamps in this dataset)" if s.get("format") in ("wfcommons-start", "travistorrent") else "Accuracy as plans progress"}</h2><p class="muted">{"Each plan has one genuine start-time observation; individual job timeline data was not available." if s.get("format") in ("wfcommons-start", "travistorrent") else "Descriptive only: later snapshots are more frequent for longer plans and are not an independent per-plan benchmark."}</p>
<table><thead><tr><th>Phase</th><th>Snapshots</th><th>Baseline MAE (min)</th><th>Selected MAE (min)</th></tr></thead><tbody>{phase_html}</tbody></table></div>
<div class="panel"><h2>20 worst plan-start errors</h2><table><thead><tr><th>Plan</th><th>Actual (min)</th><th>Baseline</th><th>Selected</th><th>Signed error</th><th>Used</th></tr></thead><tbody>{worst_html}</tbody></table></div>
<div class="panel"><h2>Reproducibility and limitations</h2><p><strong>Input:</strong> <code>{source}</code><br><strong>Scope:</strong> {scope}<br><strong>Queue:</strong> {queue}<br><strong>Warmup:</strong> {s['warmup_plans_excluded']} plans excluded from scored comparisons</p>
<p class="note">River-selected count: {s['river_used_plans']} / {s['plans_evaluated']}. Shadow-ready: {s.get('river_shadow_ready_plans', 0)}. If zero are selected, the reported selected ETA uses the baseline, but shadow accuracy remains available in summary.json on a matched eligible subset. Workflow traces cannot prove scheduler queue accuracy unless actual queue admission times exist. Plan-start-only benchmarks do not validate the production progress-aware feature extractor.</p>
<p>Raw output: <a href="summary.json">summary.json</a> · <a href="predictions.csv">predictions.csv</a>. A real benchmark requires real public executions, not the bundled synthetic CSV fixture.</p></div>
</main></body></html>'''
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding='utf-8')
    return destination
