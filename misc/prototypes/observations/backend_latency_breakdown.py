# Backend LLM latency per call vs prompt size, completion size and time of day, per run (agent event log).
# python3 misc/prototypes/observations/backend_latency_breakdown.py
import json, collections, statistics as st
from datetime import datetime, timezone
A = '/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/logs/'
RUNS = {'sil800': (A + 'fba_voice_events.jsonl', 'pine-fba-voice-paired-airline-regular-sil800'),
        'norm': (A + 'fba_voice_norm_events.jsonl', 'pine-fba-voice-norm-airline-regular')}
out = {}
for name, (ev, model) in RUNS.items():
    recs = [json.loads(l) for l in open(ev) if l.strip()]
    sids = {r['session_id'] for r in recs if r.get('kind') == 'session_start' and r.get('model') == model}
    steps = [r for r in recs if r.get('session_id') in sids and r['kind'] == 'agent_turn_done' and (r.get('backend') or {}).get('calls')]
    fe = [r for r in recs if r.get('session_id') in sids and r['kind'] == 'agent_turn_done' and (r.get('frontend') or {}).get('calls')]
    one = [s for s in steps if s['backend']['calls'] == 1]   # single-call steps: latency is one LLM call
    def lat(s): return s['backend']['latency_ms'] / 1000
    buckets = collections.defaultdict(list)
    for s in one:
        p = s['backend']['prompt_tokens']
        buckets['<4k' if p < 4000 else '4-6k' if p < 6000 else '6-8k' if p < 8000 else '8-12k' if p < 12000 else '>=12k'].append(lat(s))
    hours = collections.defaultdict(list)
    for s in one:
        hours[datetime.fromtimestamp(s['timestamp'], timezone.utc).strftime('%H')].append(lat(s))
    turns = collections.defaultdict(lambda: [0, 0.0])
    for s in steps:
        k = (s['session_id'], s['turn_id']); turns[k][0] += s['backend']['calls']; turns[k][1] += lat(s)
    out[name] = dict(
        single_call_steps=len(one),
        per_call_s=dict(mean=round(st.mean(map(lat, one)), 2), median=round(st.median(map(lat, one)), 2),
                        p90=round(sorted(map(lat, one))[int(.9 * len(one))], 2)),
        prompt_tokens_per_call=round(st.mean(s['backend']['prompt_tokens'] for s in one)),
        completion_tokens_per_call=round(st.mean(s['backend']['completion_tokens'] for s in one)),
        s_per_1k_completion=round(st.mean(lat(s) / max(s['backend']['completion_tokens'], 1) * 1000 for s in one), 2),
        by_prompt_size={k: (len(v), round(st.median(v), 2)) for k, v in sorted(buckets.items())},
        by_hour_utc={k: (len(v), round(st.median(v), 2)) for k, v in sorted(hours.items())},
        calls_per_turn=round(st.mean(v[0] for v in turns.values()), 2),
        turns_by_calls=dict(sorted(collections.Counter(min(v[0], 6) for v in turns.values()).items())),
        frontend_per_call_s=round(st.mean(r['frontend']['latency_ms'] / r['frontend']['calls'] / 1000 for r in fe), 2),
    )
print(json.dumps(out, indent=1))
