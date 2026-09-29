# Selectivity events split by tau2's underlying interruption-event type (agent speaking vs silent) and by
# whether the tic/speech is standalone or inside a longer user utterance.
# uv run python misc/prototypes/observations/selectivity_breakdown.py RUN...
import collections, json, re, sys
from pathlib import Path
from tau2.data_model.simulation import Results
from tau2.metrics.voice_interaction_metrics import (InteractionMetricsConfig, extract_all_segments,
    extract_interruption_events, extract_out_of_turn_effects, filter_end_of_conversation_ticks)

cfg = InteractionMetricsConfig()
TIC = re.compile(r'\[(cough|sneeze|sniffle|throat|laugh|sigh)[^\]]*\]', re.I)
out = {}
for run in sys.argv[1:]:
    c = collections.Counter(); ex = collections.defaultdict(list)
    for sim in Results.load(Path(f'data/simulations/{run}')).simulations:
        ft = filter_end_of_conversation_ticks(sim.ticks)
        us, ag = extract_all_segments(ft, cfg.tick_duration_sec)
        oot = extract_out_of_turn_effects(ft, cfg.tick_duration_sec)
        for e in extract_interruption_events(us, ag, ft, cfg.tick_duration_sec, out_of_turn_effects=oot):
            t = e.event_type
            if t in ('agent_interrupts_user', 'user_interrupts_agent'):
                continue
            words = TIC.sub('', e.interrupter_transcript or '').strip(' .,')
            pos = 'standalone' if not words else 'in_utterance'
            if t in ('backchannel', 'vocal_tic', 'non_directed_speech'):
                key = (t, 'agent speaking', 'ERROR yielded' if e.interrupted_yielded else 'ok kept talking', pos)
            else:
                key = (t, 'agent silent', 'ERROR' if t.startswith('agent_responds') else 'ok', pos)
            c[key] += 1
            if len(ex[key]) < 4:
                ex[key].append((int(sim.task_id), round(e.interrupter_start_time_sec, 1), (e.interrupter_transcript or '')[-70:]))
    out[run] = {' | '.join(k): dict(n=v, examples=ex[k]) for k, v in sorted(c.items())}
json.dump(out, sys.stdout, indent=1)
