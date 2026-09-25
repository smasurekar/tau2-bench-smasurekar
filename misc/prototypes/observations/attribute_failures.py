# Attribute failed tool calls and failed tasks to causes (observations doc §4.2).
# Run from the tau2 repo root: python3 misc/prototypes/observations/attribute_failures.py
import json,glob,re,collections
from datetime import datetime
EV='/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/logs/fba_voice_events.jsonl'
recs=[json.loads(l) for l in open(EV) if l.strip()]
sess={r['session_id']:r['timestamp'] for r in recs if r.get('kind')=='session_start' and r.get('model')=='pine-fba-voice-paired-airline-regular'}
tasks={t['id']:t for t in json.load(open('data/tau2/domains/airline/tasks.json'))}
ts=lambda s: datetime.fromisoformat(s).timestamp()
NUM=dict(zero='0',oh='0',one='1',two='2',three='3',four='4',five='5',six='6',seven='7',eight='8',nine='9',underscore='_')
norm=lambda t: ''.join(NUM.get(w,w) for w in t.lower().replace('.',' ').replace(',',' ').split())
CALL=collections.Counter(); TASK=collections.Counter(); CONTRIB=collections.Counter(); per={}
nfail=0; tot_err=0
for f in glob.glob('data/simulations/fba_voice_paired_airline_regular/simulations/*.json'):
    d=json.load(open(f)); tid=d['task_id']; sc=json.dumps(tasks[tid]['user_scenario'])
    uid=re.search(r'[a-z]+_[a-z]+_\d{4}',sc).group(0)
    rids=set(re.findall(r'\b[A-Z0-9]{6}\b',sc))
    a,b=ts(d['start_time']),ts(d['end_time']); sid=[s for s,x in sess.items() if a-5<=x<=b][-1]
    ev=[r for r in recs if r.get('session_id')==sid]
    out={r['call_id']:r.get('output','') for r in ev if r['kind']=='tool_output_in'}
    asr=[]; seen=set(); labels=collections.Counter(); auth=False
    for r in ev:
        if r['kind']=='asr_final': asr.append(r.get('transcript') or '')
        if r['kind']!='backend_tool_calls': continue
        heard=norm(' '.join(asr))
        for c in r['calls']:
            o=out.get(c['id'],'')
            err=o.startswith('Error')
            if c['name']=='get_user_details' and not err and c['arguments'].get('user_id')==uid: auth=True
            if not err: continue
            tot_err+=1
            arg=c['arguments']; key=(c['name'],json.dumps(arg,sort_keys=True))
            if c['name']=='get_user_details':
                x=arg.get('user_id','')
                parts=[p for p in re.split(r'_',x) if p]
                if x.lower()==uid: lab='1 case only'
                elif key in seen: lab='2 retry of an ID that already failed'
                elif len(parts)<3 or not re.fullmatch(r'\d{4}',parts[-1] if parts else ''): lab='3 incomplete ID (turn fragment)'
                elif uid in heard: lab='4 frontend garbled an ID ASR had heard correctly'
                else: lab='5 ASR mis-heard letters'
            elif c['name']=='get_reservation_details':
                x=arg.get('reservation_id','')
                if key in seen: lab='2 retry of an ID that already failed'
                elif len(x)!=6: lab='3 incomplete ID (turn fragment)'
                elif any(rid.lower() in heard for rid in rids if rid.upper()==x.upper()) or x.upper() in rids: lab='6 other'
                elif any(rid.lower() in heard for rid in rids): lab='4 frontend garbled an ID ASR had heard correctly'
                else: lab='5 ASR mis-heard letters'
            else: lab='6 other'
            seen.add(key); CALL[lab]+=1; labels[lab]+=1
    rw=(d.get('reward_info') or {}).get('reward')
    if rw!=1.0:
        nfail+=1
        if auth: p='E authenticated, then agent errors (stateless loops, lost auth, no write)'
        elif not any(c for c in labels): p='F never asked for / looked up the ID'
        elif labels['1 case only']: p='A blocked only by upper case (right characters)'
        elif labels['4 frontend garbled an ID ASR had heard correctly'] or uid in norm(' '.join(asr)): p='B ASR heard the ID; lost in turn fragments / frontend'
        else: p='C ASR never heard the ID correctly'
        TASK[p]+=1; per[int(tid)]=(p[0],d['termination_reason'])
        for l in labels: CONTRIB[l]+=1
        if d['termination_reason']=='too_many_errors': CONTRIB['(ended by 10-error cap)']+=1
print('FAILED TOOL CALLS total',tot_err)
for k,v in sorted(CALL.items()): print(f'  {k}: {v} ({100*v/tot_err:.0f}%)')
print('FAILED TASKS',nfail)
for k,v in sorted(TASK.items()): print(f'  {k}: {v} ({100*v/nfail:.0f}%)', sorted(t for t,(p,_) in per.items() if p==k[0]))
print('CONTRIBUTING (tasks with >=1 such error, of failed tasks)')
for k,v in sorted(CONTRIB.items()): print(f'  {k}: {v} ({100*v/nfail:.0f}%)')
