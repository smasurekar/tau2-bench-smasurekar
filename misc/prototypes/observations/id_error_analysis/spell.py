import json, glob, re, sys, collections
from datetime import datetime
EV='/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/logs/fba_voice_events.jsonl'
recs=[json.loads(l) for l in open(EV) if l.strip()]
by=collections.defaultdict(list)
for r in recs: by[r.get('session_id')].append(r)
tasks={t['id']:t for t in json.load(open('data/tau2/domains/airline/tasks.json'))}
ts=lambda s: datetime.fromisoformat(s).timestamp()
conf=collections.Counter()
for run in sys.argv[1:]:
    model='pine-'+run.replace('_','-')
    starts={r['session_id']:r['timestamp'] for r in recs if r.get('kind')=='session_start' and r.get('model')==model}
    st=collections.Counter()
    for f in glob.glob(f'data/simulations/{run}/simulations/*.json'):
        d=json.load(open(f))
        uid=re.search(r'[a-z]+_[a-z]+_\d{4}',json.dumps(tasks[d['task_id']]['user_scenario'])).group(0)
        parts=uid.split('_')[:2]
        a,b=ts(d['start_time']),ts(d['end_time'])
        sid=[s for s,x in sorted(starts.items(),key=lambda kv:kv[1]) if a-5<=x<=b][-1]
        for r in by[sid]:
            if r['kind']!='asr_final': continue
            t=(r.get('transcript') or '').lower()
            if 'underscore' not in t: continue
            segs=[s.strip() for s in re.split(r'underscore',t)]
            for p in parts:
                # find a segment whose compacted letters are "about" this part (same first letter or length within 2)
                for s in segs:
                    toks=re.findall(r'[a-z]+',s)
                    if not toks: continue
                    # tail tokens nearest the underscore form the part
                    single=[x for x in toks if len(x)==1]
                    spelled = len(single)>=max(2,len(toks)-1)
                    cand=''.join(toks[-len(p):]) if spelled else toks[-1]
                    if spelled:
                        # take trailing single letters
                        tail=[]
                        for x in reversed(toks):
                            if len(x)<=2: tail.insert(0,x)
                            else: break
                        cand=''.join(tail)
                    if abs(len(cand)-len(p))>2 or not cand: continue
                    if cand[0]!=p[0] and cand[-1]!=p[-1]: continue
                    mode='spelled' if spelled else 'word'
                    st[(mode,cand==p)]+=1
                    if spelled and cand!=p and len(cand)==len(p):
                        for x,y in zip(p,cand):
                            if x!=y: conf[(x,y)]+=1
                    break
    for m in ('word','spelled'):
        ok,bad=st[(m,True)],st[(m,False)]
        print(f'{run}: name part said as {m:7}: {ok+bad:3} instances, correct {ok} ({100*ok/max(1,ok+bad):.0f}%)')
print('letter substitutions in spelled name parts (expected -> heard):', conf.most_common(15))
