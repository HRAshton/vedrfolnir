import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from vedrfolnir.benchmarks import (
    Job, Workload, csv_plans, swf_plans, wfcommons_plans, evaluate, main
)
from vedrfolnir.source import CSVSource


def make_plan(plan_id, start, duration=600, queue=120):
    queued = start
    started = start + timedelta(seconds=queue)
    ended = start + timedelta(seconds=duration)
    job = Job(run_id=plan_id + ':0', plan_id=plan_id, job_id='worker',
              queued_at=queued, started_at=started, finished_at=ended)
    return Workload(plan_id, start, ended, (job,))


def test_csv_fixture_and_file_source():
    root = Path(__file__).resolve().parents[1]
    plans = csv_plans(root/'examples'/'benchmarks')
    assert len(plans) >= 60
    assert all(len(p.jobs) >= 2 for p in plans)
    src = CSVSource(root/'examples'/'data')
    first = src.historical(datetime(2000, 1, 1, tzinfo=timezone.utc), '', 10)
    assert len(first) == 10
    assert (first[0]['finished_at'], first[0]['run_id']) <= (first[-1]['finished_at'], first[-1]['run_id'])
    tail = src.historical(first[-1]['finished_at'], first[-1]['run_id'], 10)
    assert len(tail) == 10
    assert set(x['run_id'] for x in first).isdisjoint(x['run_id'] for x in tail)
    assert src.live(datetime.now(timezone.utc)) == ([], [])


def test_wfcommons_parser_does_not_invent_queue_times(tmp_path):
    path = tmp_path/'workflow.json'
    path.write_text(json.dumps({
        'workflow': {'specification': {'tasks': [{'id': 'a', 'name': 'compile'},
                                                  {'id': 'b', 'name': 'link'}]},
                     'execution': {'executedAt': '2026-01-01T00:00:00Z', 'tasks': [
                         {'id': 'a', 'executedAt': '2026-01-01T00:00:00Z', 'runtimeInSeconds': 120},
                         {'id': 'b', 'executedAt': '2026-01-01T00:03:00Z', 'runtimeInSeconds': 240},
                     ]}}
    }))
    workflows = wfcommons_plans(tmp_path)
    assert len(workflows) == 1
    assert len(workflows[0].jobs) == 2
    assert {j.job_id for j in workflows[0].jobs} == {'compile', 'link'}
    assert all(j.queued_at is None for j in workflows[0].jobs)
    assert (workflows[0].finished_at-workflows[0].started_at).total_seconds() == 420


def test_swf_parser_uses_real_wait_not_future_start(tmp_path):
    path = tmp_path/'jobs.swf'
    path.write_text('; example SWF header\n'
                    '1 100 20 40 2 0 0 0 0 0 1 0 0 0 0 0 0 0\n'
                    '2 101 0 60 8 0 0 0 0 0 0 0 0 0 0 0 0 0\n'
                    '3 102 -1 30 2 0 0 0 0 0 1 0 0 0 0 0 0 0\n')
    plans = swf_plans(path)
    assert len(plans) == 2
    assert (plans[0].jobs[0].started_at-plans[0].started_at).total_seconds() == 20
    assert plans[1].jobs[0].status == 'failed'


def test_benchmark_chronology_and_warmup():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    early = make_plan('early', now, 600)
    late = make_plan('late', now+timedelta(hours=4), 100000, queue=100)
    # The later job must not alter a prediction made four hours earlier.
    a = evaluate([early], warmup=0, min_training=1000, offsets=(0, 60))
    b = evaluate([early, late], warmup=0, min_training=1000, offsets=(0, 60))
    assert a['forecasts'][0]['baseline_minutes'] == b['forecasts'][0]['baseline_minutes']
    assert b['summary']['plans_total'] == 2
    assert b['summary']['plans_evaluated'] == 2
    assert all(r['actual_minutes'] > 0 for r in b['forecasts'])
    assert evaluate([early,late], warmup=1, offsets=(0,))['summary']['plans_evaluated'] == 1


def test_benchmark_cli_writes_reproducible_outputs(tmp_path, capsys):
    root = Path(__file__).resolve().parents[1]
    out = tmp_path/'results'
    main(['--format', 'csv', '--input', str(root/'examples'/'benchmarks'),
          '--output', str(out), '--max-plans', '8', '--warmup', '2', '--min-training', '2'])
    result = json.loads((out/'summary.json').read_text())
    with (out/'predictions.csv').open() as stream:
        predictions = list(csv.DictReader(stream))
    assert result['plans_evaluated'] == 6
    assert result['benchmark_scope'] == 'multi-job plan ETA'
    assert len(predictions) >= 8
    assert result['baseline']['mae_minutes'] is not None


def test_bad_csv_and_invalid_plan_rejected(tmp_path):
    (tmp_path/'historical_jobs.csv').write_text('run_id\na\n')
    with pytest.raises(ValueError, match='missing columns'):
        CSVSource(tmp_path).fetch('historical_jobs')
    now = datetime(2026,1,1,tzinfo=timezone.utc)
    p = Workload('x', now, now+timedelta(seconds=60),
                 (Job('job','different','worker',now,now,now+timedelta(seconds=10)),))
    with pytest.raises(ValueError, match='membership'):
        from vedrfolnir.benchmarks import _valid
        _valid(p)


def test_wfcommons_reports_empty_and_invalid_json(tmp_path):
    with pytest.raises(ValueError, match='No JSON execution files'):
        wfcommons_plans(tmp_path)
    (tmp_path/'metadata.json').write_text('{"description":"not a workflow"}')
    with pytest.raises(ValueError, match='Found 1 JSON files.*zero valid.*metadata.json'):
        wfcommons_plans(tmp_path)


def test_wfcommons_skips_unrelated_json_and_hides_future_vm(tmp_path):
    from vedrfolnir.benchmarks import _make_job
    (tmp_path/'metadata.json').write_text('{"foo":1}')
    (tmp_path/'valid.json').write_text(json.dumps({
        'workflow': {'specification': {'tasks': [{'id': 'a', 'name': 'build'}]},
                     'execution': {'tasks': [{'id': 'a', 'executedAt': '2026-01-01T01:00:00Z',
                                               'runtimeInSeconds': 60, 'machines':['node-1']}]}}
    }))
    plans = wfcommons_plans(tmp_path)
    assert len(plans) == 1
    task = plans[0].jobs[0]
    assert _make_job(task, task.started_at-timedelta(seconds=10)).vm_name == 'unknown'
    assert _make_job(task, task.started_at).vm_name == 'node-1'


def test_report_and_local_one_command_benchmark(tmp_path, capsys):
    from vedrfolnir.benchmark_quickstart import main as quickstart_main
    trace = tmp_path/'data'
    trace.mkdir()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(4):
        ts = start + timedelta(hours=i*2)
        payload = {'workflow': {'specification': {'tasks': [{'id': 'a', 'name': 'compile'},
                                                             {'id': 'b', 'name': 'link'}]},
                               'execution': {'tasks': [
                                   {'id':'a','executedAt':ts.isoformat(), 'runtimeInSeconds':60+i*10},
                                   {'id':'b','executedAt':(ts+timedelta(minutes=3)).isoformat(), 'runtimeInSeconds':180+i*12},
                               ]}}}
        (trace/f'{i}.json').write_text(json.dumps(payload))
    output = tmp_path/'result'
    assert quickstart_main(['--data', str(trace), '--output', str(output), '--warmup','1',
                            '--min-training','1','--max-plans','4','--no-open']) == 0
    assert (output/'summary.json').exists()
    assert (output/'predictions.csv').exists()
    report = (output/'report.html').read_text(encoding='utf-8')
    assert 'Absolute error distribution' in report
    assert '20 worst plan-start errors' in report
    assert 'Workflow traces cannot prove scheduler queue accuracy' in report
    assert 'River-selected plans' in report
    assert 'Detailed offline report' in capsys.readouterr().out


def test_report_excludes_cancelled_plans(tmp_path):
    from vedrfolnir.benchmark_report import write_report
    a = {'plan_id': 'a', 'plan_index': 0, 'actual_minutes': 50,
         'baseline_minutes': 30, 'predicted_minutes': 32, 'phase': 'start', 'river_used': False}
    b = {**a, 'plan_id': 'cancelled', 'plan_index': 1,
         'actual_minutes': 10000, 'baseline_minutes': 1, 'predicted_minutes': 1}
    report = tmp_path / 'report.html'
    result = {'summary': {'plans_evaluated': 1, 'plans_total': 2,
                          'warmup_plans_excluded': 0, 'river_used_plans': 0,
                          'benchmark_scope': 'multi-job plan ETA', 'queue_observability': 'unknown'},
              'forecasts': [a,b], 'scored_plan_ids': ['a']}
    write_report(result, report)
    text = report.read_text()
    assert 'cancelled</td>' not in text
    assert '20.0' in text


def test_pegasus_real_layout_supports_workflow_start_benchmark(tmp_path):
    """Pegasus WfInstances: per-task runtime but NO per-task executedAt."""
    from vedrfolnir.benchmarks import wfcommons_planstart, evaluate_planstart
    folder = tmp_path / '1000genome'
    folder.mkdir()
    for i in range(6):
        start = datetime(2020, 4, 1, tzinfo=timezone.utc) + timedelta(hours=4*i)
        doc = {
            'name': f'1000genome-{i}', 'schemaVersion': '1.6',
            'workflow': {
                'specification': {'tasks': [
                    {'id': 'individuals_ID0000001', 'name': 'individuals_ID0000001', 'parents': []},
                    {'id': 'merge_ID0000002', 'name': 'merge_ID0000002', 'parents': ['individuals_ID0000001']}
                ], 'metrics': {'numberOfLevels': 2, 'maximumWidth': 1}},
                'execution': {'executedAt': start.strftime('%Y%m%dT%H%M%S+0000'),
                              'makespanInSeconds': 120.0 + i * 10,
                              'tasks': [{'id': 'individuals_ID0000001', 'runtimeInSeconds': 50},
                                        {'id': 'merge_ID0000002', 'runtimeInSeconds': 40}]}
            }
        }
        (folder / f'execution-{i}.json').write_text(json.dumps(doc))
    plans = wfcommons_planstart(tmp_path)
    assert len(plans) == 6
    assert plans[0].family == '1000genome'
    assert plans[0].task_count == 2
    assert plans[0].features['plan_total_jobs'] == 2.0
    assert 'runtimeInSeconds' not in plans[0].features
    result = evaluate_planstart(plans, warmup=2, min_training=2)
    assert result['summary']['plans_evaluated'] == 4
    assert result['summary']['snapshots_generated'] == 6
    assert result['summary']['river_used_plans'] == 0  # six workflows cannot validate a 40-build shadow gate
    assert result['summary']['river_shadow_ready_plans'] >= 1
    assert result['summary']['river_only']['count'] == result['summary']['baseline_on_river_subset']['count']
    assert all(x['phase'] == 'start' for x in result['forecasts'])
    assert result['forecasts'][0]['actual_minutes'] == 2.0


def test_planstart_does_not_leak_future_workflow_results(tmp_path):
    from vedrfolnir.benchmarks import WorkflowSummary, evaluate_planstart
    t = datetime(2020, 1, 1, tzinfo=timezone.utc)
    feat = {'plan_total_jobs': 4.0, 'log_plan_total_jobs': 1.6}
    a = WorkflowSummary('a', 'family', t, t + timedelta(minutes=10), 4, feat)
    b = WorkflowSummary('b', 'family', t + timedelta(minutes=2), t + timedelta(minutes=1000), 4, feat)
    c = WorkflowSummary('c', 'family', t + timedelta(minutes=20), t + timedelta(minutes=24), 4, feat)
    short = evaluate_planstart([a,c], warmup=0, min_training=999)
    long = evaluate_planstart([a,b,c], warmup=0, min_training=999)
    # B has started but has not completed at C's start. It cannot train C.
    get = lambda result: next(r for r in result['forecasts'] if r['plan_id'] == 'c')
    assert get(short)['baseline_minutes'] == get(long)['baseline_minutes']
    assert get(short)['predicted_minutes'] == get(long)['predicted_minutes']


def test_pegasus_start_format_generates_html_report(tmp_path):
    from vedrfolnir.benchmarks import main
    traces = tmp_path / 'pegasus'
    traces.mkdir()
    for i in range(4):
        (traces / f'{i}.json').write_text(json.dumps({'workflow': {
          'specification': {'tasks': [{'id':'a','name':'download','parents':[]}]},
          'execution': {'executedAt':f'2020-01-0{i+1}T00:00:00Z',
                        'makespanInSeconds':60*(i+1),
                        'tasks':[{'id':'a','runtimeInSeconds':60}]}}}))
    out = tmp_path / 'out'
    main(['--format', 'wfcommons-start', '--input',str(traces), '--output',str(out),
          '--warmup','1','--min-training','1','--max-plans','4'])
    summary = json.loads((out/'summary.json').read_text())
    assert summary['plans_evaluated'] == 3
    assert summary['benchmark_scope'] == 'workflow-start ETA only; no task progress'
    assert (out/'report.html').exists()
    assert len(list(csv.DictReader((out/'predictions.csv').open()))) == 4


def test_pegasus_quickstart_uses_workflow_start_mode(tmp_path, capsys):
    from vedrfolnir.benchmark_quickstart import main as quickstart_main
    folder = tmp_path / 'pegasus' / 'montage'
    folder.mkdir(parents=True)
    for i in range(5):
        (folder / f'{i}.json').write_text(json.dumps({'workflow': {
            'specification': {'tasks': [
                {'name': 'task_ID0000001', 'id': 'task_ID0000001', 'parents': []}]},
            'execution': {'executedAt': f'2020-03-0{i + 1}T00:00:00Z',
                          'makespanInSeconds': 40 * (i + 1),
                          'tasks': [{'id': 'task_ID0000001', 'runtimeInSeconds': 20}]}}}))
    out = tmp_path / 'out'
    assert quickstart_main(['--data', str(tmp_path / 'pegasus'), '--output', str(out),
                            '--warmup', '1', '--min-training', '1',
                            '--max-plans', '5', '--no-open']) == 0
    summary = json.loads((out / 'summary.json').read_text())
    assert summary['format'] == 'wfcommons-start'
    assert summary['plans_evaluated'] == 4
    assert 'workflow-start ETA only' in summary['benchmark_scope']
    html = (out / 'report.html').read_text()
    assert 'Plan-start only (no progress timestamps in this dataset)' in html
    assert 'Dataset has workflow start and makespan' in capsys.readouterr().out


def test_public_planstart_requires_completed_out_of_sample_shadow_cohort():
    from vedrfolnir.benchmarks import WorkflowSummary, evaluate_planstart
    t = datetime(2025, 1, 1, tzinfo=timezone.utc)
    f = {'plan_total_jobs': 1.0, 'log_plan_total_jobs': 0.69}
    plans = [
        WorkflowSummary(str(i), 'same-family',
                        t + timedelta(hours=i),
                        t + timedelta(hours=i, minutes=10 + i % 5),
                        1, f)
        for i in range(24)
    ]
    result = evaluate_planstart(plans, warmup=4, min_training=2)
    s = result["summary"]
    assert s["river_shadow_ready_plans"] > 0
    assert s["river_used_plans"] == 0
    assert s["selected_predictor"] == s["baseline"]
    assert s["river_only"]["count"] == s["baseline_on_river_subset"]["count"]
    assert all(row["predicted_minutes"] == row["baseline_minutes"]
               for row in result["forecasts"])
