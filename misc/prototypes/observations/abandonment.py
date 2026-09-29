# How calls end: was the agent still working when the call ended, and how often did the user ping
# ("any update?", "are you still there?") because nothing came back. Both runs.
# python3 misc/prototypes/observations/abandonment.py
import json, re, collections, sys
sys.path.insert(0, 'misc/prototypes/observations')
src = open('misc/prototypes/observations/agent_state_at_events.py').read().split('\nout = {}')[0]
ns = {}; exec(src, ns)
PING = re.compile(r"any ?up ?dates?|still there|are you there|you there\b|hello\b|can't hear|call back|still here|cut out", re.I)
res = {}
for name, (run, evlog) in ns['RUNS'].items():
    S = ns['sessions'](run, evlog)
    rew = {}
    import glob
    for f in glob.glob(f'data/simulations/{run}/simulations/*.json'):
        d = json.load(open(f)); rew[int(d['task_id'])] = (d.get('reward_info') or {}).get('reward')
    rows = {}
    for task, ev in S.items():
        tl = ns['turns'](ev)
        end = max((r['audio_ms'] for r in ev if 'audio_ms' in r), default=0) / 1000
        user = [r.get('transcript', '') for r in ev if r['kind'] == 'asr_final' and r.get('transcript', '').strip()]
        pings = sum(bool(PING.search(u)) for u in user)
        last3 = user[-3:]
        rows[task] = dict(reward=rew[task], working_at_end=ns['state_at'](tl, end - 0.3)['state'] == 'working',
                          pings=pings, ends_with_ping=any(PING.search(u) for u in last3[-2:]), user_turns=len(user))
    agg = {}
    for label, sel in (('passed', lambda x: x['reward'] == 1), ('failed', lambda x: x['reward'] != 1)):
        v = [x for x in rows.values() if sel(x)]
        agg[label] = dict(n=len(v), working_at_end=sum(x['working_at_end'] for x in v),
                          ends_with_ping=sum(x['ends_with_ping'] for x in v),
                          pings_per_task=round(sum(x['pings'] for x in v) / max(len(v), 1), 1))
    res[name] = dict(summary=agg, per_task=rows)
json.dump(res, open('/tmp/ia/abandonment.json', 'w'), indent=1)
print(json.dumps({k: v['summary'] for k, v in res.items()}, indent=1))
