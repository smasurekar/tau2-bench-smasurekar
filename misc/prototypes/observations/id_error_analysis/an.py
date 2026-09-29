import json, glob, re, sys, collections, difflib
from datetime import datetime
EV='/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/logs/fba_voice_events.jsonl'
recs=[json.loads(l) for l in open(EV) if l.strip()]
by=collections.defaultdict(list)
for r in recs: by[r.get('session_id')].append(r)
tasks={t['id']:t for t in json.load(open('data/tau2/domains/airline/tasks.json'))}
ts=lambda s: datetime.fromisoformat(s).timestamp()
NUM=dict(zero='0',oh='0',o='0',one='1',two='2',three='3',four='4',five='5',six='6',seven='7',eight='8',nine='9',underscore='_')
def norm(t):  # ASR text -> compact char stream
    t=t.lower().replace('.',' ').replace(',',' ').replace('-',' ').replace('?',' ')
    return ''.join(NUM.get(w,w) for w in t.split())
K=lambda s: re.sub(r'[^a-z0-9]','',s.lower())
run=sys.argv[1]
model='pine-'+run.replace('_','-')
starts={r['session_id']:r['timestamp'] for r in recs if r.get('kind')=='session_start' and r.get('model')==model}
rows=[]
for f in glob.glob(f'data/simulations/{run}/simulations/*.json'):
    d=json.load(open(f)); tid=int(d['task_id'])
    uid=re.search(r'[a-z]+_[a-z]+_\d{4}',json.dumps(tasks[d['task_id']]['user_scenario'])).group(0)
    a,b=ts(d['start_time']),ts(d['end_time'])
    sid=[s for s,x in sorted(starts.items(),key=lambda kv:kv[1]) if a-5<=x<=b][-1]
    asr=[]; lastq=''; seen=set()
    for r in by[sid]:
        k=r['kind']
        if k=='asr_final': asr.append(r.get('transcript') or '')
        elif k=='delegation': lastq=str(r.get('query') or '')
        elif k=='backend_tool_calls':
            for c in r['calls']:
                if c['name']!='get_user_details': continue
                x=c['arguments'].get('user_id','')
                heard=K(norm(' '.join(asr)))
                recent=K(norm(' '.join(asr[-4:])))
                rows.append(dict(task=tid,uid=uid,x=x,asr_all_has=K(uid) in heard, asr_recent=' | '.join(asr[-4:])[-260:],
                    fe_q=lastq[:300], fe_has=K(uid) in K(lastq), retry=x in seen, reward=(d.get('reward_info') or {}).get('reward')))
                seen.add(x)
json.dump(rows,open(f'/tmp/idan/{run}.json','w'),indent=1)
print(len(rows))
