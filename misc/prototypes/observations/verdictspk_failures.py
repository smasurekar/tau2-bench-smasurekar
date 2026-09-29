# Per-task failure data and timelines for the verdictspk airline run (frontend barge-in verdict, audible filler).
# Run from the tau2 repo root:
#   python3 misc/prototypes/observations/verdictspk_failures.py OUT_DIR
# Writes OUT_DIR/tasks.json (one record per task) and OUT_DIR/timelines/task_<id>.txt (user speech, agent
# hearing, delegations, tool calls, answers and barge-in verdict events on one audio clock).
# Sessions are joined to simulations with the I2 join (joinfix report), role "primary".
import collections
import csv
import glob
import json
import os
import re
import sys

RUN = 'fba_voice_verdictspk_airline_regular'
EV = '/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/logs/fba_voice_verdictspk_events.jsonl'
JOIN = f'data/simulations/_metrics/{RUN}_report_joinfix2/join.csv'
READ = {'get_user_details', 'get_reservation_details', 'search_direct_flight', 'search_onestop_flight',
        'list_all_airports', 'calculate', 'get_flight_status'}
out_dir = sys.argv[1]
os.makedirs(f'{out_dir}/timelines', exist_ok=True)

tasks = {t['id']: t for t in json.load(open('data/tau2/domains/airline/tasks.json'))}
by = collections.defaultdict(list)
for line in open(EV):
    if line.strip():
        r = json.loads(line)
        by[r.get('session_id')].append(r)
primary = {row['sim_id']: row['session_id'] for row in csv.DictReader(open(JOIN)) if row['role'] == 'primary'}


def short(x, n=160):
    x = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    return x if len(x) <= n else x[:n] + '…'


def user_labels(tid, sim_id):
    path = glob.glob(f'data/simulations/{RUN}/artifacts/task_{tid}/sim_{sim_id}/audio/user_labels.txt')
    rows = []
    for line in open(path[0]) if path else []:
        parts = line.rstrip('\n').split('\t')
        if len(parts) >= 3:
            rows.append((float(parts[0]), float(parts[1]), parts[2]))
    return rows


records = {}
for f in sorted(glob.glob(f'data/simulations/{RUN}/simulations/*.json')):
    d = json.load(open(f))
    tid, sim_id = d['task_id'], d['id']
    t = tasks[tid]
    ri = d.get('reward_info') or {}
    ev = by[primary[sim_id]]
    scen = json.dumps(t['user_scenario'])
    m = re.search(r'[a-z]+_[a-z]+_\d{4}', scen)
    uid = m.group(0) if m else ''
    outs = {r['call_id']: r.get('output', '') for r in ev if r['kind'] == 'tool_output_in'}
    calls = [c for r in ev if r['kind'] == 'backend_tool_calls' for c in r['calls']]
    sent = [c for c in calls if c['id'] in outs]
    err = lambda c: outs[c['id']].startswith('Error')
    ids_tried = [c['arguments'].get('user_id') for c in sent if c['name'] == 'get_user_details']
    exp = t['evaluation_criteria'].get('actions') or []
    verdicts = [r for r in ev if r['kind'] == 'barge_in_verdict']
    labels = user_labels(tid, sim_id)
    rec = dict(
        task_id=int(tid), sim_id=sim_id, session=primary[sim_id], reward=ri.get('reward'),
        termination=d['termination_reason'], duration=round(d.get('duration') or 0),
        breakdown=ri.get('reward_breakdown'), db_match=(ri.get('db_check') or {}).get('db_match'),
        action_checks=[(a['action']['name'], a['action_match']) for a in ri.get('action_checks') or []],
        nl=[(x.get('nl_assertion'), x.get('met')) for x in (ri.get('nl_assertions') or [])],
        expected_user_id=uid,
        logged_in=any(c['name'] == 'get_user_details' and not err(c) and c['arguments'].get('user_id') == uid for c in sent),
        user_ids_tried=ids_tried,
        tool_calls=len(sent), tool_errors=sum(err(c) for c in sent),
        expected_actions=[(a['name'], a.get('arguments')) for a in exp],
        expected_writes=[a['name'] for a in exp if a['name'] not in READ],
        done_writes=[(c['name'], c['arguments']) for c in sent if c['name'] not in READ and not err(c)],
        failed_writes=[(c['name'], outs[c['id']][:120]) for c in sent if c['name'] not in READ and err(c)],
        reason_for_call=t['user_scenario']['instructions'].get('reason_for_call', ''),
        task_instructions=t['user_scenario']['instructions'].get('task_instructions', ''),
        nl_expected=t['evaluation_criteria'].get('nl_assertions') or [],
        verdicts=[dict(v=r['verdict'], reason=r['reason'], state=r.get('task_state'), words=r.get('utterance'),
                       running=short(r.get('running_query') or '', 120), audio_ms=r.get('audio_ms')) for r in verdicts],
        staged_committed=sum(r['kind'] == 'staged_committed' for r in ev),
        staged_discarded=sum(r['kind'] == 'staged_discarded' for r in ev),
        cancelled=collections.Counter(r.get('reason') for r in ev if r['kind'] == 'thinking_cancelled'),
        direct_answers=[r['text'] for r in ev if r['kind'] == 'direct_answer'],
        agent_failures=[(r['kind'], short(r.get('error') or r.get('discarded_text') or r.get('missing') or '', 120))
                        for r in ev if r['kind'] in ('backend_error', 'tool_result_timeout', 'frontend_contract_violation')],
        last_user=[x[2] for x in labels[-4:]],
        last_agent=[r.get('text', '') for r in ev if r['kind'] in ('backend_final', 'direct_answer')][-2:],
    )
    records[int(tid)] = rec

    # timeline, ordered by wall time. Agent events carry wall time; user speech (tau2 labels, sim seconds) is
    # placed by interpolating sim time -> wall time over agent events that have both clocks.
    import bisect
    t0 = next((r['timestamp'] for r in ev if r['kind'] == 'session_start'), ev[0]['timestamp'] if ev else 0)
    pairs = sorted(((r['audio_ms'] / 1000.0, r['timestamp'] - t0) for r in ev if r.get('audio_ms') is not None))
    xs = [a for a, _ in pairs]

    def to_wall(sim_s):
        if not pairs:
            return sim_s
        i = bisect.bisect_left(xs, sim_s)
        if i <= 0:
            return pairs[0][1] - (pairs[0][0] - sim_s)
        if i >= len(pairs):
            return pairs[-1][1] + (sim_s - pairs[-1][0])
        (a0, w0), (a1, w1) = pairs[i - 1], pairs[i]
        return w0 if a1 == a0 else w0 + (w1 - w0) * (sim_s - a0) / (a1 - a0)

    lines = [(to_wall(s), f'USER SAYS  sim {s:6.1f}-{e:6.1f}: {txt}') for s, e, txt in labels]
    last_audio = 0.0
    for r in ev:
        k = r['kind']
        if r.get('audio_ms') is not None:
            last_audio = r['audio_ms'] / 1000.0
        wall = r['timestamp'] - t0
        tag = f'w{wall:5.0f} s{last_audio:6.1f}'
        if k == 'asr_final':
            lines.append((wall, f'  heard    {tag} "{r.get("transcript")}"'))
        elif k == 'delegation':
            lines.append((wall, f'  DELEGATE {tag} {short(r.get("query"), 220)}'))
        elif k == 'filler':
            lines.append((wall, f'  filler   {tag} {short(r.get("text"))}'))
        elif k == 'backend_tool_calls':
            for c in r['calls']:
                lines.append((wall, f'  TOOL     {tag} {c["name"]}({short(c["arguments"], 140)}) -> {short(outs.get(c["id"], "<no output>"), 140)}'))
        elif k in ('backend_final', 'direct_answer'):
            lines.append((wall, f'  AGENT    {tag} [{k}] {short(r.get("text"), 300)}'))
        elif k == 'barge_in_verdict':
            lines.append((wall, f'  VERDICT  {tag} {r["verdict"]}/{r["reason"]} state={r.get("task_state")} words="{r.get("utterance")}" running="{short(r.get("running_query") or "", 100)}" probe="{short(r.get("probe_query") or "", 100)}"'))
        elif k in ('thinking_cancelled', 'staged_committed', 'staged_discarded', 'barge_in_review', 'input_queued',
                   'backend_error', 'tool_result_timeout', 'frontend_contract_violation', 'turn_staged'):
            extra = {x: r[x] for x in ('reason', 'text', 'filler_spoken', 'chars', 'error', 'discarded_text') if x in r}
            lines.append((wall, f'  {k:<8} {tag} {short(extra, 200)}'))
    lines.sort(key=lambda x: x[0])
    ri_txt = json.dumps({k: rec[k] for k in ('reward', 'termination', 'breakdown', 'db_match', 'action_checks', 'nl')}, default=str)
    head = [f'TASK {tid}  {ri_txt}',
            f'REASON FOR CALL: {rec["reason_for_call"]}',
            f'TASK INSTRUCTIONS: {rec["task_instructions"]}',
            f'EXPECTED USER ID: {uid}  logged_in={rec["logged_in"]}  ids_tried={ids_tried}',
            f'EXPECTED ACTIONS: {json.dumps(rec["expected_actions"])}',
            f'DONE WRITES: {json.dumps(rec["done_writes"])}  FAILED WRITES: {json.dumps(rec["failed_writes"])}',
            f'NL EXPECTED: {rec["nl_expected"]}', '']
    with open(f'{out_dir}/timelines/task_{tid}.txt', 'w') as fh:
        fh.write('\n'.join(head + [x[1] for x in lines]) + '\n')

json.dump(records, open(f'{out_dir}/tasks.json', 'w'), indent=1, default=str)
fails = sorted(k for k, v in records.items() if not v['reward'])
print(f'{len(records)} tasks, {len(fails)} with reward 0: {fails}')
