# Per-task comparison of the 800 ms paired run and the norm run (login funnel, endings, backend call latency).
# Run from the tau2 repo root: python3 misc/prototypes/observations/compare_norm.py > compare_norm.json
import json, glob, re, collections, statistics
from datetime import datetime

A = '/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/logs/'
RUNS = {'sil800': ('fba_voice_paired_airline_regular_sil800', A + 'fba_voice_events.jsonl'),
        'norm': ('fba_voice_norm_airline_regular', A + 'fba_voice_norm_events.jsonl')}
tasks = {t['id']: t for t in json.load(open('data/tau2/domains/airline/tasks.json'))}
ts = lambda s: datetime.fromisoformat(s).timestamp()


def run(name, evlog):
    recs = [json.loads(l) for l in open(evlog) if l.strip()]
    by = collections.defaultdict(list)
    for r in recs:
        by[r.get('session_id')].append(r)
    model = 'pine-' + name.replace('_', '-')
    starts = sorted((r['timestamp'], r['session_id']) for r in recs if r.get('kind') == 'session_start' and r.get('model') == model)
    out, be_call_ms = {}, []
    for f in glob.glob(f'data/simulations/{name}/simulations/*.json'):
        d = json.load(open(f)); tid = d['task_id']
        uid = re.search(r'[a-z]+_[a-z]+_\d{4}', json.dumps(tasks[tid]['user_scenario'])).group(0)
        a, b = ts(d['start_time']), ts(d['end_time'])
        sid = [s for x, s in starts if a - 5 <= x <= b][-1]
        ev = by[sid]
        outs = [r.get('output', '') for r in ev if r['kind'] == 'tool_output_in']
        tau2_lookups = [o for o in outs if o.startswith('Error: User') or o.startswith('{"user_id"')]
        for r in ev:
            if r['kind'] == 'agent_turn_done' and r.get('backend', {}).get('calls'):
                be_call_ms.append(r['backend']['latency_ms'] / r['backend']['calls'])
        out[int(tid)] = dict(
            reward=(d.get('reward_info') or {}).get('reward'), term=d['termination_reason'],
            auth=any(o.startswith('{"user_id": "%s"' % uid) for o in outs),
            tau2_errors=sum(o.startswith('Error') for o in outs),
            failed_lookups_at_tau2=sum(o.startswith('Error: User') for o in tau2_lookups),
            local_answers=collections.Counter(r.get('reason') for r in ev if r['kind'] == 'call_answered_locally'),
            duration=round(d.get('duration') or 0))
    return out, be_call_ms


res = {}
for k, (name, ev) in RUNS.items():
    per_task, be = run(name, ev)
    res[k] = per_task
    res[k + '_backend_ms_per_llm_call'] = dict(n=len(be), mean=round(statistics.mean(be)), median=round(statistics.median(be)))
json.dump(res, open('/dev/stdout', 'w'), indent=1, default=str)
