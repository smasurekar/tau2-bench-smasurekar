import json, glob, re, sys, collections
from datetime import datetime
EV='/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/logs/fba_voice_events.jsonl'
recs=[json.loads(l) for l in open(EV) if l.strip()]
by=collections.defaultdict(list)
for r in recs: by[r.get('session_id')].append(r)
tasks={t['id']:t for t in json.load(open('data/tau2/domains/airline/tasks.json'))}
ts=lambda s: datetime.fromisoformat(s).timestamp()
NUM=dict(zero='0',oh='0',one='1',two='2',three='3',four='4',five='5',six='6',seven='7',eight='8',nine='9',underscore='_')
def norm(t):
    t=t.lower()
    for ch in '.,-?!': t=t.replace(ch,' ')
    return ''.join(NUM.get(w,w) for w in t.split())
def words(t): return re.findall(r"[a-z]+", t.lower())
run=sys.argv[1]; model='pine-'+run.replace('_','-')
starts={r['session_id']:r['timestamp'] for r in recs if r.get('kind')=='session_start' and r.get('model')==model}
out=collections.Counter(); per={}
tic=0; tic_ids=0; asr_total=0
for f in glob.glob(f'data/simulations/{run}/simulations/*.json'):
    d=json.load(open(f)); tid=int(d['task_id'])
    uid=re.search(r'[a-z]+_[a-z]+_\d{4}',json.dumps(tasks[d['task_id']]['user_scenario'])).group(0)
    first,last,dig=uid.split('_')
    a,b=ts(d['start_time']),ts(d['end_time'])
    sid=[s for s,x in sorted(starts.items(),key=lambda kv:kv[1]) if a-5<=x<=b][-1]
    ev=by[sid]
    asr=[r.get('transcript') or '' for r in ev if r['kind']=='asr_final']
    asr_total+=len(asr); tic+=sum(bool(re.search(r'\bh\.?\s?k\.?\s?k|hkk|hkh|ahem|a ham',x.lower())) for x in asr)
    ids=[c['arguments'].get('user_id','') for r in ev if r['kind']=='backend_tool_calls' for c in r['calls'] if c['name']=='get_user_details']
    tic_ids+=sum(bool(re.search(r'h_?k|ahem',x.lower())) for x in ids)
    auth=any(x==uid for x in ids)
    case=any(x.lower()==uid for x in ids)
    fmt=any(re.sub(r'[^a-z0-9]','',x.lower())==uid.replace('_','') for x in ids)
    single=any(uid in norm(x) for x in asr)
    win=any(uid in norm(' '.join(asr[i:i+k])) for k in (2,3,4) for i in range(len(asr)))
    # spelled-ID assembler ignoring separators the ASR inserted inside a part
    compact=lambda s: re.sub(r'[^a-z0-9_]','',s)
    win_loose=any(uid in compact(norm(' '.join(asr[i:i+k]))) for k in (1,2,3,4) for i in range(len(asr)))
    W=set(w for x in asr for w in words(x))
    allnorm=norm(' '.join(asr))
    name_spoken=first in W and last in W
    digits_heard=dig in re.sub(r'[^0-9]','',allnorm) or dig in allnorm
    # nearest homophone spoken
    if auth: cat='0 authenticated'
    elif case: cat='1 right chars, wrong case (lower-case fixes)'
    elif fmt: cat='2 right chars, extra separators/dots (canonicaliser fixes)'
    elif single or win or win_loose: cat='3 ASR had the full ID, split over turns or not used (assembler fixes)'
    elif name_spoken and digits_heard: cat='4 ASR had the spoken name + digits, spelling wrong (name reconciliation)'
    else: cat='5 ASR never had it (acoustic)'
    out[cat]+=1; per[tid]=(cat[0], d['reward_info']['reward'] if d.get('reward_info') else None, name_spoken, digits_heard)
print(run)
for k in sorted(out): print(f'  {k}: {out[k]}')
for c in '12345': print('   ',c, sorted(t for t,v in per.items() if v[0]==c))
print('  never-auth tasks: spoken name heard', sum(v[2] for v in per.values() if v[0]!='0'),'digits heard',sum(v[3] for v in per.values() if v[0]!='0'))
print(f'  ASR finals with cough/tic letters (hkk/ahem): {tic}/{asr_total}; user-ID lookups containing HK/ahem: {tic_ids}')
json.dump(per,open(f'/tmp/idan/per_{run}.json','w'))
