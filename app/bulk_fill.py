"""Fill existing voids first, then copy an exactly validated identical-bag layout.
Only names vary within a batch. No dimensions, clearances or load limits are relaxed.
"""
import copy,json

def split(packages):
    groups={};normal=[]
    for p in packages:
        if not p.get('void_fill'):normal.append(p);continue
        key=json.dumps({k:v for k,v in p.items() if k!='name'},sort_keys=True,ensure_ascii=False,default=str)
        groups.setdefault(key,[]).append(p)
    bulk=[g for g in groups.values() if len(g)>=256]
    for g in groups.values():
        if len(g)<256:normal.extend(g)
    return normal,bulk

def pack(m,packages,c):
    normal,groups=split(packages)
    if not groups:return None
    states=[]
    for pkg in m._ordered_packages(normal):
        if not states or not m._place_one(pkg,states):
            st=m._new_bin_state(c);states.append(st)
            if not m._place_one(pkg,[st]):return None
    def risk_score(candidate):
        audits=[m._container_state_audit(s) for s in candidate]
        return (*m._audit_severity_counts(audits)[:2],sum(max(a['offset_x_pct'],a['offset_y_pct']) for a in audits),len(candidate))
    # Resolve equipment-only imbalance before lightweight bags occupy the remaining voids.
    if states and any(m._audit_severity_counts([m._container_state_audit(s) for s in states])[:2]):
        best=risk_score(states)
        for count in (len(states),len(states)+1):
            alternative=m.pack_specific_bins_multistart(normal,[dict(c) for _ in range(count)])
            if alternative and risk_score(alternative)<best:states=alternative;best=risk_score(states)
            if best[:2]==(0,0):break
    filled=0;replicated=0
    for group in groups:
        pending=list(group);index=0
        # Only use genuine solver points and full physical checks on existing loads.
        for st in states:
            while index<len(pending) and m._place_one(pending[index],[st]):index+=1;filled+=1
        if index==len(pending):continue
        template=m._new_bin_state(c);start=index
        while index<len(pending) and m._place_one(pending[index],[template]):index+=1
        capacity=index-start
        if not capacity:return None
        states.append(template)
        while index<len(pending):
            n=min(capacity,len(pending)-index)
            if n<capacity:
                st=m._new_bin_state(c)
                for pkg in pending[index:index+n]:
                    if not m._place_one(pkg,[st]):return None
            else:
                st=copy.deepcopy(template)
                for q,pkg in zip(st['placements'],pending[index:index+n]):q['name']=pkg['name']
                st['reject_stats']={};replicated+=1
            states.append(st);index+=n
    plan=m._decorate_plan(states,c['code'],c['code'])
    plan['void_fill_audit']={'bags_in_existing_voids':filled,'identical_layout_copies':replicated,'note':'逐件原尺寸；先填已有柜中满足支撑/顶载的空位，再增加柜数；相同物理输入复用完整校核布局，不假定袋材压缩或袋上叠放。'}
    return plan
