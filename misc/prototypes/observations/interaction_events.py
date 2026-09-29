# Dump tau2's per-event interaction metrics (the events behind R_R, R_Y, L_R, L_Y, I_A, S_*) for a voice run.
# Run from the tau2 repo root: uv run python misc/prototypes/observations/interaction_events.py RUN OUT.json
import json, sys
from pathlib import Path
from tau2.data_model.simulation import Results
from tau2.metrics.voice_interaction_metrics import (
    InteractionMetricsConfig, extract_all_segments, extract_interruption_events,
    extract_voice_quality_events_from_simulation, extract_turn_transitions, filter_end_of_conversation_ticks)

run, out = sys.argv[1:3]
cfg = InteractionMetricsConfig()
res = Results.load(Path(f'data/simulations/{run}'))
rows = []
for sim in res.simulations:
    for e in extract_voice_quality_events_from_simulation(sim.ticks, cfg.tick_duration_sec, task_id=sim.task_id, simulation_id=sim.id):
        rows.append(dict(src='vq', task=int(sim.task_id), cat=e.event_category, type=e.event_type, err=e.is_error,
                         lat=e.latency_sec, t=e.event_time_sec, text=e.transcript))
    fus, fag = extract_all_segments(filter_end_of_conversation_ticks(sim.ticks), cfg.tick_duration_sec)
    for tt in extract_turn_transitions(fus, fag):
        rows.append(dict(src='tt', task=int(sim.task_id), outcome=tt.outcome, t=tt.user_end_time_sec, gap=tt.gap_sec,
                         text=tt.user_transcript, next_t=tt.next_user_start_time_sec, agent_t=tt.agent_start_time_sec))
    us, ag = extract_all_segments(sim.ticks, cfg.tick_duration_sec)
    for e in extract_interruption_events(us, ag, sim.ticks, cfg.tick_duration_sec):
        if e.event_type == 'agent_interrupts_user':
            rows.append(dict(src='ia', task=int(sim.task_id), cat='agent_interrupts_user', type=e.event_type,
                             t=e.interrupter_start_time_sec, dur=e.interrupter_duration_sec, text=e.interrupter_transcript))
    rows.append(dict(src='sim', task=int(sim.task_id), n_user=len(us), n_agent=len(ag), ticks=len(sim.ticks),
                     user=[(s.start_time_sec if hasattr(s, 'start_time_sec') else None, getattr(s, 'end_time_sec', None), getattr(s, 'transcript', '')) for s in us],
                     agent=[(getattr(s, 'start_time_sec', None), getattr(s, 'end_time_sec', None), getattr(s, 'transcript', '')) for s in ag]))
json.dump(rows, open(out, 'w'), default=str)
print(run, len(rows))
