# Where along the pipeline the user ID got lost, per task (verdictspk airline run).
# For each task: did the user say the ID (tau2 labels), did the ASR produce it (one utterance / across
# consecutive utterances), did the frontend put it into a delegation query (any case / exact case), and did
# the backend call get_user_details with it. Text is normalised: lower case, number words -> digits,
# "underscore" -> "_", everything else but [a-z0-9_] dropped, tokens joined.
# python3 misc/prototypes/observations/verdictspk_id_stage.py ANALYSIS_DIR
import collections, csv, glob, json, re, sys
A = sys.argv[1]
RUN = 'fba_voice_verdictspk_airline_regular'
J = f'data/simulations/_metrics/{RUN}_report_joinfix2/join.csv'
E = '/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/logs/fba_voice_verdictspk_events.jsonl'
W = dict(zero='0', oh='0', o='0', one='1', two='2', three='3', four='4', five='5', six='6', seven='7', eight='8', nine='9',
         underscore='_')
def norm(t):
    toks = re.findall(r"[a-z0-9_]+", t.lower().replace('-', ' '))
    return ''.join(W.get(x, x) for x in toks)
def norm_keep_o(t):  # variant where a lone "o" stays a letter
    toks = re.findall(r"[a-z0-9_]+", t.lower().replace('-', ' '))
    return ''.join(W.get(x, x) if x != 'o' else 'o' for x in toks)
tasks = json.load(open(f'{A}/tasks.json'))
prim = {r['session_id']: r for r in csv.DictReader(open(J)) if r['role'] == 'primary'}
ev = collections.defaultdict(list)
for l in open(E):
    if l.strip():
        x = json.loads(l)
        if x['session_id'] in prim:
            ev[x['session_id']].append(x)
rows = []
for k, r in sorted(tasks.items(), key=lambda kv: int(kv[0])):
    uid = r['expected_user_id']
    m = ev[r['session']]
    lab = glob.glob(f"data/simulations/{RUN}/artifacts/task_{k}/sim_{r['sim_id']}/audio/user_labels.txt")
    said = [l.split('\t')[2] for l in open(lab[0]) if l.count('\t') >= 2] if lab else []
    heard = [x.get('transcript') or '' for x in m if x['kind'] == 'asr_final']
    dq = [x.get('query') or '' for x in m if x['kind'] == 'delegation'] + [x.get('text') or '' for x in m if x['kind'] == 'direct_answer']
    ids = [c['arguments'].get('user_id') or '' for x in m if x['kind'] == 'backend_tool_calls' for c in x['calls'] if c['name'] == 'get_user_details']
    hit = lambda texts, f=norm: any(uid in f(t) or uid in norm_keep_o(t) for t in texts)
    user_ok = hit(said) or uid in norm(' '.join(said))
    asr_one = hit(heard)
    asr_join = any(uid in norm(' '.join(heard[i:i + 3])) for i in range(len(heard)))
    fe_any = any(uid in re.sub(r'[^a-z0-9_]', '', q.lower()) or uid in q.lower() for q in dq)
    fe_exact = any(uid in q for q in dq)
    be_exact = uid in ids
    be_ci = any(i.lower() == uid for i in ids)
    # attribute by the last stage the ID survived
    case_origin = ''
    if be_ci and not be_exact:
        wrong = [i for i in ids if i.lower() == uid and i != uid]
        case_origin = 'frontend' if any(w in q for w in wrong for q in dq) else 'backend'
    if not uid:
        stage = 'n/a (no user ID in task)'
    elif r['logged_in']:
        stage = 'logged in'
    elif not ids and not fe_any:
        stage = 'no lookup made'
    elif be_ci:
        stage = f'CASE (right ID, wrong letter case; upper-cased by {case_origin})'
    elif fe_any:
        stage = 'BACKEND (delegation had it; never called with it)'
    elif asr_one or asr_join:
        stage = 'FRONTEND (ASR had it; never passed on)' if asr_one else 'ASR-split + FRONTEND (pieces across utterances, never assembled)'
    elif user_ok:
        stage = 'ASR (never transcribed correctly)'
    else:
        stage = 'USER-SIM (never said it cleanly)'
    rows.append(dict(task=int(k), reward=r['reward'], uid=uid, logged_in=r['logged_in'], user_said=user_ok, asr_one_utt=asr_one,
                     asr_across=asr_join, frontend_any_case=fe_any, frontend_exact=fe_exact, backend_any_case=be_ci,
                     backend_exact=be_exact, lookups=len(ids), stage=stage, sample_ids=sorted(set(ids))[:4]))
with open(f'{A}/id_stage.csv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
fails = [x for x in rows if not x['reward']]
print('stage (all 50):', dict(collections.Counter(x['stage'] for x in rows)))
print('stage (33 failed):', dict(collections.Counter(x['stage'] for x in fails)))
for x in fails:
    print(f"t{x['task']:>2} {x['stage']:<60} said={x['user_said']!s:<5} asr1={x['asr_one_utt']!s:<5} asrX={x['asr_across']!s:<5} fe={x['frontend_any_case']!s:<5} feExact={x['frontend_exact']!s:<5} be={x['backend_any_case']!s:<5} {x['sample_ids']}")
