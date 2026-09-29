# Per-task failure table for the norm run: login, expected vs completed writes, DB match, NL/communicate.
# python3 misc/prototypes/observations/norm_failures.py > norm_failures.json
import json, glob, re, collections
from datetime import datetime
EV = '/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/logs/fba_voice_norm_events.jsonl'
RUN = 'fba_voice_norm_airline_regular'
READ = {'get_user_details', 'get_reservation_details', 'search_direct_flight', 'search_onestop_flight',
        'list_all_airports', 'calculate', 'get_flight_status'}
tasks = {t['id']: t for t in json.load(open('data/tau2/domains/airline/tasks.json'))}
ts = lambda s: datetime.fromisoformat(s).timestamp()
recs = [json.loads(l) for l in open(EV) if l.strip()]
by = collections.defaultdict(list)
for r in recs:
    by[r.get('session_id')].append(r)
starts = sorted((r['timestamp'], r['session_id']) for r in recs if r.get('kind') == 'session_start' and r.get('model') == 'pine-' + RUN.replace('_', '-'))
out = {}
for f in glob.glob(f'data/simulations/{RUN}/simulations/*.json'):
    d = json.load(open(f)); tid = d['task_id']; t = tasks[tid]; ri = d.get('reward_info') or {}
    a, b = ts(d['start_time']), ts(d['end_time'])
    ev = by[[s for x, s in starts if a - 5 <= x <= b][-1]]
    uid = re.search(r'[a-z]+_[a-z]+_\d{4}', json.dumps(t['user_scenario'])).group(0)
    outs = {r['call_id']: r.get('output', '') for r in ev if r['kind'] == 'tool_output_in'}
    calls = [c for r in ev if r['kind'] == 'backend_tool_calls' for c in r['calls']]
    sent = [c for c in calls if c['id'] in outs]
    done_w = [(c['name'], c['arguments']) for c in sent if c['name'] not in READ and not outs[c['id']].startswith('Error')]
    fail_w = [(c['name'], outs[c['id']][:80]) for c in sent if c['name'] not in READ and outs[c['id']].startswith('Error')]
    exp = [(x['name'], x.get('arguments')) for x in (t['evaluation_criteria'].get('actions') or []) if x['name'] not in READ]
    out[int(tid)] = dict(
        reward=ri.get('reward'), term=d['termination_reason'], duration=round(d.get('duration') or 0),
        auth=any(o.startswith('{"user_id": "%s"' % uid) for o in outs.values()),
        breakdown=ri.get('reward_breakdown'), db_match=(ri.get('db_check') or {}).get('db_match'),
        nl=[(x.get('nl_assertion'), x.get('met')) for x in (ri.get('nl_assertions') or [])],
        expected_writes=[n for n, _ in exp], done_writes=[n for n, _ in done_w], failed_writes=fail_w,
        expected_args=exp, done_args=done_w,
        purpose=t['user_scenario']['instructions'].get('reason_for_call', '')[:200] if isinstance(t['user_scenario'].get('instructions'), dict) else '',
        last_agent=[r.get('text', '')[:160] for r in ev if r['kind'] in ('backend_final', 'direct_answer')][-2:],
    )
json.dump(out, open('/dev/stdout', 'w'), indent=1, default=str)
