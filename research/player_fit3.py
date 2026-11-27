import json,glob,math,os,sys
from collections import defaultdict
sys.path.insert(0,'/home/claude/jarvis/research')
import player_shots_backtest as B
def load(path):
    out=[]
    for f in sorted(glob.glob(os.path.join(path,'*.json'))):
        js=json.load(open(f,encoding='utf-8'))
        acc=defaultdict(lambda:{'min':0.0,'shots':0,'sot':0,'starts':0,'start_min':0.0})
        ms=sorted(js['matches'],key=lambda m:m['utc'])
        for i,m in enumerate(ms):
            for p in m['players']:
                a=acc[p['id']]
                if p['started'] and a['min']>=450 and p.get('minutes') is not None:
                    out.append({'half':'fit' if i<len(ms)/2 else 'test','mins_so_far':a['min'],
                        'shots_tot':a['shots'],'sot_tot':a['sot'],
                        'shots_p90':a['shots']*90/a['min'],'sot_p90':a['sot']*90/a['min'],
                        'avg_start_min':a['start_min']/a['starts'] if a['starts'] else 80.0,
                        'shots':p['shots'],'sot':p['sot']})
            for p in m['players']:
                mm=p.get('minutes')
                if mm is None: continue
                a=acc[p['id']];a['min']+=mm;a['shots']+=p['shots'];a['sot']+=p['sot']
                if p['started']: a['starts']+=1;a['start_min']+=mm
    return out
S=[x for x in load(sys.argv[1]) if x['shots_p90']>=1.5]
fit=[x for x in S if x['half']=='fit'];test=[x for x in S if x['half']=='test']
POP={s:sum(x[f'{s}_tot'] for x in fit)*90/sum(x['mins_so_far'] for x in fit) for s in ('shots','sot')}
print('population per 90:',{k:round(v,3) for k,v in POP.items()},'fit',len(fit),'test',len(test))
LINES={'sot':(1,2),'shots':(1,2,3)}
def lam(x,s,k,mult):
    # shrink the season-to-date rate toward the population mean with k 90-minute units of prior
    rate=(x[f'{s}_tot']+k*POP[s])/(x['mins_so_far']/90+k)
    return max(rate*x['avg_start_min']/90*mult,1e-6)
def score(S_,s,k,mult,r,calib=False):
    ll=n=0;cal=defaultdict(lambda:[0,0.0,0])
    for x in S_:
        L=lam(x,s,k,mult)
        for j in LINES[s]:
            p=min(max(B.p_atleast(j,L,r),1e-6),1-1e-6);y=x[s]>=j
            ll-=math.log(p if y else 1-p);n+=1
            if calib:
                b=cal[(j,min(int(p*10),9))];b[0]+=1;b[1]+=p;b[2]+=y
    o={'ll':round(ll/n,5)}
    if calib: o['cal']={f'{j}+ p{b/10:.1f}':[v[0],round(v[1]/v[0],3),round(v[2]/v[0],3),round(v[2]/v[0]-v[1]/v[0],3)] for (j,b),v in sorted(cal.items()) if v[0]>=150}
    return o
res={}
for s in ('shots','sot'):
    best=min(((score(fit,s,k,m_,r)['ll'],k,m_,r) for k in (0,1,2,3,5,8,12,20) for m_ in (0.925,0.95,0.975,1.0,1.025) for r in (2,3,4,6,10,20,None)))
    _,k,m_,r=best
    p_=score(test,s,0,1.0,3,True);f_=score(test,s,k,m_,r,True)
    print('== %s  fitted k=%s mult=%.3f r=%s   fit ll %.5f'%(s,k,m_,r,best[0]))
    print('   test ll production %.5f -> %.5f'%(p_['ll'],f_['ll']))
    print('   prod ',' '.join('%s:%+.3f'%(a,b[3]) for a,b in p_['cal'].items()))
    print('   fit  ',' '.join('%s:%+.3f'%(a,b[3]) for a,b in f_['cal'].items()))
    res[s]={'k':k,'mult':m_,'r':r,'testProd':p_['ll'],'testFit':f_['ll'],'pop':POP[s],
            'calProd':p_['cal'],'calFit':f_['cal']}
json.dump(res,open('/home/claude/jarvis/research/player_fit3.json','w'),indent=1)
