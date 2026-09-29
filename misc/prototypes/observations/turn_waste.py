# Cancelled turns (cancel_and_merge) and whether backend work was thrown away; frontend answers that claim a
# completed action with no write tool call in the session; repeated local answers for the same ID.
# python3 misc/prototypes/observations/turn_waste.py
import json, re, collections, statistics as st, sys
src = open('misc/prototypes/observations/agent_state_at_events.py').read().split('\nout = {}')[0]
ns = {}; exec(src, ns)
CLAIM = re.compile(r"\b(has|have) been (cancel|book|upgrad|updat|chang|process|refund|modif|remov|add)|\ball done\b|\bi've (cancel|book|upgrad|updat|chang|process|refund|modif|remov|add)", re.I)
WRITE = {'book_reservation', 'cancel_reservation', 'update_reservation_flights', 'update_reservation_passengers',
         'update_reservation_baggages', 'send_certificate'}
res = {}
for name, (run, evlog) in ns['RUNS'].items():
    S = ns['sessions'](run, evlog)
    canc = 0; canc_after_backend = 0; since = []; claims = []; loops = collections.Counter()
    for task, ev in S.items():
        delegated = set(); start = {}
        writes = 0
        last_local = None; streak = 0
        for r in ev:
            k = r['kind']; tid = r.get('turn_id')
            if k == 'agent_turn_start':
                start[tid] = r['audio_ms']
            elif k == 'agent_turn_done' and (r.get('backend') or {}).get('calls'):
                delegated.add(tid)
            elif k == 'delegation':
                delegated.add(max(start) if start else None)
            elif k == 'thinking_cancelled':
                canc += 1
                if tid in delegated:
                    canc_after_backend += 1; since.append((r['audio_ms'] - start.get(tid, r['audio_ms'])) / 1000)
            elif k == 'backend_tool_calls':
                writes += sum(c['name'] in WRITE for c in r['calls'])
            elif k == 'direct_answer' and CLAIM.search(r.get('text', '')) and writes == 0:
                claims.append((task, r['text'][:150]))
            elif k == 'call_answered_locally':
                key = (r.get('reason'), r.get('value'))
                streak = streak + 1 if key == last_local else 1; last_local = key
                loops[task] = max(loops[task], streak)
    res[name] = dict(cancelled=canc, cancelled_after_backend_started=canc_after_backend,
                     backend_seconds_before_cancel_median=round(st.median(since), 1) if since else None,
                     frontend_action_claims_without_write=claims,
                     tasks_with_same_local_answer_3plus=sorted((t, n) for t, n in loops.items() if n >= 3))
print(json.dumps(res, indent=1))
