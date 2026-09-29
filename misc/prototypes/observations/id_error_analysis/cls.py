import json,re,sys,collections,difflib
K=lambda s: re.sub(r'[^a-z0-9]','',s.lower())
def classify(r):
    x,u=r['x'],r['uid']
    if x==u: return 'correct'
    if x.lower()==u: return 'case only'
    kx,ku=K(x),K(u)
    if kx==ku: return 'format only (dots/spaces/separators)'
    if kx and kx in ku: return 'incomplete (fragment of the ID)'
    nm,dg=u.rsplit('_',1)
    if len(kx)>len(ku)+4: return 'extra words/noise in ID'
    return 'wrong characters'
def stage(r):
    if r['asr_all_has']:
        return 'FE/BE lost it (ASR had it)' if not r['fe_has'] else 'BE lost it (FE query had it)'
    return 'ASR never had it'
run=sys.argv[1]
rows=json.load(open(f'{run}.json'))
C=collections.Counter(); U=collections.Counter(); S=collections.Counter(); uniq={}
for r in rows:
    c=classify(r); C[c]+=1
    key=(r['task'],r['x'])
    if key not in uniq: uniq[key]=r
for r in uniq.values():
    c=classify(r)
    if c in('correct',): continue
    U[c]+=1; S[(c,stage(r))]+=1
print(run); print(' all calls:',dict(C)); print(' unique IDs per task:',dict(U))
for k,v in sorted(S.items()): print('   ',k,v)
