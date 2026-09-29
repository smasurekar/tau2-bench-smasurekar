# Join tau2 interaction events (from interaction_events.py) to the agent's timeline: what was the agent doing
# when the user gave up waiting (no_response), when it spoke over the user (agent_interrupts_user), and at
# selectivity errors. The agent's audio_ms clock counts received user audio, i.e. tau2 simulated time.
# python3 misc/prototypes/observations/agent_state_at_events.py > agent_state.json
import json, glob, collections, statistics as st
from datetime import datetime
A = '/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/logs/'
RUNS = {'sil800': ('fba_voice_paired_airline_regular_sil800', A + 'fba_voice_events.jsonl'),
        'norm': ('fba_voice_norm_airline_regular', A + 'fba_voice_norm_events.jsonl')}
ts = lambda s: datetime.fromisoformat(s).timestamp()


def sessions(run, evlog):
    recs = [json.loads(l) for l in open(evlog) if l.strip()]
    by = collections.defaultdict(list)
    for r in recs:
        by[r.get('session_id')].append(r)
    model = 'pine-' + run.replace('_', '-')
    starts = sorted((r['timestamp'], r['session_id']) for r in recs if r.get('kind') == 'session_start' and r.get('model') == model)
    m = {}
    for f in glob.glob(f'data/simulations/{run}/simulations/*.json'):
        d = json.load(open(f)); a, b = ts(d['start_time']), ts(d['end_time'])
        m[int(d['task_id'])] = by[[s for x, s in starts if a - 5 <= x <= b][-1]]
    return m


def turns(ev):
    """Per agent turn: start (sim s), first answer audio (sim s), cancelled (sim s), phases."""
    T = collections.OrderedDict()
    last_audio = 0
    for r in ev:
        if 'audio_ms' in r:
            last_audio = r['audio_ms']
        k = r['kind']; tid = r.get('turn_id')
        if k == 'agent_turn_start':
            T[tid] = dict(start=r['audio_ms'] / 1000, audio=None, cancelled=None, local=0, tool_rounds=0, text=r.get('text', ''))
        elif k == 'turn_latency' and tid in T and T[tid]['audio'] is None:
            T[tid]['audio'] = r['audio_ms'] / 1000
        elif k == 'thinking_cancelled' and tid in T:
            T[tid]['cancelled'] = r['audio_ms'] / 1000
        elif k == 'tool_calls_out' and tid in T:
            T[tid]['tool_rounds'] += 1
        elif k == 'call_answered_locally' and T:
            list(T.values())[-1]['local'] += 1
    return list(T.values())


def state_at(tl, t):
    """What the agent was doing at simulated time t."""
    live = [x for x in tl if x['start'] <= t + 0.05 and (x['audio'] is None or x['audio'] > t) and (x['cancelled'] is None or x['cancelled'] > t)]
    if live:
        x = live[-1]
        return dict(state='working', since=round(t - x['start'], 1), tool_rounds=x['tool_rounds'], local=x['local'],
                    answered_later=x['audio'] is not None, cancelled_later=x['cancelled'] is not None)
    return dict(state='idle')


out = {}
for name, (run, evlog) in RUNS.items():
    S = sessions(run, evlog)
    ev = json.load(open(f'/tmp/ia/{run}.json'))
    res = collections.defaultdict(list)
    for e in ev:
        tl = turns(S[e['task']])
        if e['src'] == 'tt' and e['outcome'] == 'no_response':
            # the user gave up at next_t: was the agent still working on something then?
            res['no_response'].append(dict(task=e['task'], t=e['t'], text=e['text'][-80:], **state_at(tl, e['next_t'] - 0.05)))
        elif e['src'] == 'ia':
            res['agent_interrupts_user'].append(dict(task=e['task'], t=e['t'], text=e['text'][-80:], **state_at(tl, e['t'] - 0.25)))
        elif e['src'] == 'vq' and e['cat'] in ('backchannel', 'vocal_tic', 'non_directed'):
            res[e['type']].append(dict(task=e['task'], t=e['t'], text=e['text'][-80:], **state_at(tl, e['t'])))
    summ = {}
    for k, v in res.items():
        c = collections.Counter(x['state'] for x in v)
        w = [x for x in v if x['state'] == 'working']
        summ[k] = dict(n=len(v), states=dict(c),
                       working_since_median=round(st.median(x['since'] for x in w), 1) if w else None,
                       working_with_tool_rounds=sum(x['tool_rounds'] > 0 for x in w),
                       working_with_local_answers=sum(x['local'] > 0 for x in w),
                       working_then_cancelled=sum(x['cancelled_later'] for x in w))
    out[name] = dict(summary=summ, events={k: v for k, v in res.items()})
json.dump(out, open('/tmp/ia/agent_state.json', 'w'), indent=1)
print(json.dumps({k: v['summary'] for k, v in out.items()}, indent=1))
