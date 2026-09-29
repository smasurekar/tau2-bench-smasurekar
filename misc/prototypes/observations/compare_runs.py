# Per-task funnel and comparison of two voice runs plus the text-2-text baselines.
# Run from the tau2 repo root:
#   python3 misc/prototypes/observations/compare_runs.py [OLD_RUN NEW_RUN] > compare.json
# Defaults: fba_voice_paired_airline_regular (500 ms) vs fba_voice_paired_airline_regular_sil800 (800 ms).
import json, glob, re, sys, collections
from datetime import datetime

EV = '/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/logs/fba_voice_events.jsonl'
OLD, NEW = (sys.argv[1:3] if len(sys.argv) > 2 else
            ('fba_voice_paired_airline_regular', 'fba_voice_paired_airline_regular_sil800'))
TEXT = {'text_paired': 'fba_paired_airline_base_4trials', 'text_bo': 'fba_backend_only_airline_base_4trials'}
READ = {'get_user_details', 'get_reservation_details', 'search_direct_flight', 'search_onestop_flight',
        'list_all_airports', 'calculate', 'get_flight_status'}

recs = [json.loads(l) for l in open(EV) if l.strip()]
by_sess = collections.defaultdict(list)
for r in recs:
    by_sess[r.get('session_id')].append(r)
tasks = {t['id']: t for t in json.load(open('data/tau2/domains/airline/tasks.json'))}
ts = lambda s: datetime.fromisoformat(s).timestamp()
NUM = dict(zero='0', oh='0', one='1', two='2', three='3', four='4', five='5', six='6', seven='7',
           eight='8', nine='9', underscore='_')
norm = lambda t: ''.join(NUM.get(w, w) for w in t.lower().replace('.', ' ').replace(',', ' ').replace('-', ' ').split())


def text_rewards(run):
    d = json.load(open(f'data/simulations/{run}/results.json'))
    out = collections.defaultdict(list)
    for s in d['simulations']:
        out[s['task_id']].append((s.get('reward_info') or {}).get('reward') == 1.0)
    return {k: f'{sum(v)}/{len(v)}' for k, v in out.items()}


def voice(run):
    model = 'pine-' + run.replace('_', '-')
    starts = {r['session_id']: r['timestamp'] for r in recs if r.get('kind') == 'session_start' and r.get('model') == model}
    out = {}
    for f in glob.glob(f'data/simulations/{run}/simulations/*.json'):
        d = json.load(open(f)); tid = d['task_id']; t = tasks[tid]
        uid = re.search(r'[a-z]+_[a-z]+_\d{4}', json.dumps(t['user_scenario'])).group(0)
        a, b = ts(d['start_time']), ts(d['end_time'])
        sid = [s for s, x in sorted(starts.items(), key=lambda kv: kv[1]) if a - 5 <= x <= b][-1]
        ev = by_sess[sid]
        asr = ' '.join(r.get('transcript') or '' for r in ev if r['kind'] == 'asr_final')
        fe = ' '.join(str(r.get('query') or '') for r in ev if r['kind'] == 'delegation').lower()
        outs = {r['call_id']: r.get('output', '') for r in ev if r['kind'] == 'tool_output_in'}
        calls = [c for r in ev if r['kind'] == 'backend_tool_calls' for c in r['calls']]
        errs = [c for c in calls if outs.get(c['id'], '').startswith('Error')]
        uid_fail = [c['arguments'].get('user_id', '') for c in errs if c['name'] == 'get_user_details']
        auth = any(c['name'] == 'get_user_details' and c['arguments'].get('user_id') == uid
                   and not outs.get(c['id'], '').startswith('Error') for c in calls)
        exp_w = [x['name'] for x in (t['evaluation_criteria'].get('actions') or []) if x['name'] not in READ]
        done_w = [c['name'] for c in calls if c['name'] not in READ and not outs.get(c['id'], '').startswith('Error')]
        fe_ids = re.findall(r'[a-z]+(?:[ _]+[a-z]+)?[ _]+\d{4}', fe)
        out[int(tid)] = dict(
            reward=(d.get('reward_info') or {}).get('reward'), term=d['termination_reason'],
            uid=uid, asr_heard=uid in norm(asr), fe_passed=uid.replace('_', '') in fe.replace('_', '').replace(' ', ''),
            auth=auth, tool_errors=len(errs), user_id_lookups=sum(c['name'] == 'get_user_details' for c in calls),
            case_only_fails=sum(x.lower() == uid and x != uid for x in uid_fail),
            first_failed_ids=list(dict.fromkeys(uid_fail))[:3], exp_writes=exp_w, done_writes=done_w,
            asr_segments=sum(r['kind'] == 'asr_final' for r in ev),
            cancelled=sum(r['kind'] == 'thinking_cancelled' for r in ev),
            turns=sum(r['kind'] == 'agent_turn_start' for r in ev),
            duration=round(d.get('duration') or 0), session=sid, sim_id=d['id'],
        )
    return out


res = {'old_run': OLD, 'new_run': NEW, 'old': voice(OLD), 'new': voice(NEW)}
for k, run in TEXT.items():
    res[k] = text_rewards(run)
json.dump(res, sys.stdout, indent=1, default=str)
