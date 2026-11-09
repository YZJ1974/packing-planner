"""User-authorized top zones above the conservative equipment skyline.

A body corridor and staging face are explicit. Top delivery uses staged handoff
or a loading aid, with required reach/headroom reported, not assumed certified.
No volume inside cylindrical bounding boxes or under unsupported overhangs is
reclaimed. This preserves the declared flexible-volume basis (8 bags/m3).
"""
import math
from app.bag_top_policy import approved
from app.physical_inputs import source_verified
from app.bag_access import overlap,EPS,WORKER_HEIGHT_MM


def rectangles(xs,ys,mask,min_l,min_w):
    free=[r[:] for r in mask];nx,ny=len(xs)-1,len(ys)-1;out=[]
    while True:
        best=None;area=0.
        for a in range(nx):
            good=[True]*ny
            for b in range(a,nx):
                good=[v and free[b][j] for j,v in enumerate(good)]
                if xs[b+1]-xs[a]<min_l-EPS:continue
                j=0
                while j<ny:
                    if not good[j]:j+=1;continue
                    c=j
                    while j<ny and good[j]:j+=1
                    w=ys[j]-ys[c];size=(xs[b+1]-xs[a])*w
                    if w>=min_w-EPS and size>area+EPS:area=size;best=(a,b+1,c,j)
        if best is None:break
        a,b,c,d=best;out.append((xs[a],ys[c],xs[b]-xs[a],ys[d]-ys[c]))
        for i in range(a,b):
            for j in range(c,d):free[i][j]=False
    return out


def support_budget(q,qs):
    if not (q.get('top_load_verified') and source_verified(q.get('top_load_source_type'))):return None
    cap=q.get('max_top_load_kg')
    if cap is None:return None
    if not math.isfinite(float(cap)) or float(cap)<0:return 0.
    z=q['z']+q['h']
    above=[r for r in qs if r is not q and r['z']>=z-EPS and min(q['x']+q['l'],r['x']+r['l'])>max(q['x'],r['x'])+EPS and min(q['y']+q['w'],r['y']+r['w'])>max(q['y'],r['y'])+EPS]
    return max(0.,float(cap)-sum(float(r.get('weight_kg',0)) for r in above))


def top_stages(box,policy,reference_dimensions=(560,560,800)):
    if not policy:return []
    L,W,H=(float(box['inner_'+a]) for a in ('l','w','h'));qs=box.get('placements',[])
    if float(box.get('door_w',W))<800 or float(box.get('door_h',H))<WORKER_HEIGHT_MM:return []
    xs=sorted({0.,L}|{max(0.,min(L,float(q['x'])+s*float(q['l']))) for q in qs for s in (0,1)})
    ys=sorted({0.,W}|{max(0.,min(W,float(q['y'])+s*float(q['w']))) for q in qs for s in (0,1)})
    groups={};support_map={};nx,ny=len(xs)-1,len(ys)-1
    for i in range(nx):
        for j in range(ny):
            covering=[k for k,q in enumerate(qs) if q['x']<xs[i+1]-EPS and q['x']+q['l']>xs[i]+EPS and q['y']<ys[j+1]-EPS and q['y']+q['w']>ys[j]+EPS]
            if not covering:continue
            z=max(qs[k]['z']+qs[k]['h'] for k in covering)
            top=tuple(k for k in covering if abs(qs[k]['z']+qs[k]['h']-z)<EPS)
            if H-z<=EPS or not all(approved(qs[k],policy) for k in top):continue
            key=(round(z,6),top)
            if key not in groups:groups[key]=[[False]*ny for _ in range(nx)]
            groups[key][i][j]=True;support_map[key]=[qs[k] for k in top]
    candidates=[]
    for (z,indices),mask in groups.items():
        # Bags may deform within their declared volume; narrow side slivers are
        # still excluded. No fixed human-body width is imposed at bag location.
        for x,y,l,w in rectangles(xs,ys,mask,min(reference_dimensions),min(reference_dimensions)):
            space=(x,y,z,l,w,H-z);supports=support_map[(z,indices)]
            # Finish a connected equipment group before sending bags onto it.
            frontier=x+l
            while True:
                installed=[q for q in qs if q['x']<frontier-EPS]
                end=max([frontier]+[q['x']+q['l'] for q in installed])
                if end<=frontier+EPS:break
                frontier=end
            if frontier>L+EPS:continue
            installed.sort(key=lambda q:(q['z'],q['x'],q['y']))
            door_w=min(W,float(box.get('door_w',W)));cy=(W-800.)/2
            corridor=dict(x=frontier,y=cy,z=0.,l=max(0.,L-frontier),w=800.,h=WORKER_HEIGHT_MM)
            # Remainder of the 800mm-deep work face may be outside the door.
            work=dict(x=frontier,y=cy,z=0.,l=800.,w=800.,h=WORKER_HEIGHT_MM)
            if any(overlap(corridor,q) or overlap(work,q) for q in installed):continue
            chain=list(supports);changed=True
            while changed:
                changed=False
                for upper in list(chain):
                    for lower in qs:
                        if lower in chain or abs(lower['z']+lower['h']-upper['z'])>EPS:continue
                        if min(lower['x']+lower['l'],upper['x']+upper['l'])>max(lower['x'],upper['x'])+EPS and min(lower['y']+lower['w'],upper['y']+upper['w'])>max(lower['y'],upper['y'])+EPS:chain.append(lower);changed=True
            budgets=[support_budget(q,qs) for q in chain]
            if any(v is not None and v<=EPS for v in budgets):continue
            finite=[v for v in budgets if v is not None]
            # Straight overhead delivery requires a loading aid/handoff reach;
            # do not label this as a checked human walk-through path.
            ceiling=max([z]+[q['z']+q['h'] for q in installed if q['x']+q['l']>x+EPS and q['y']<y+w-EPS and q['y']+q['w']>y+EPS])
            headroom=H-ceiling
            if headroom<=EPS:continue
            candidates.append(dict(space=space,kind='user_confirmed_top',frontier_mm=frontier,corridor=corridor,work_area=work,
                                   installed_sequences=[q.get('seq',i+1) for i,q in enumerate(installed)],future_sequences=[q.get('seq',i+1) for i,q in enumerate(qs) if q not in installed],
                                   support_sequence=supports[0].get('seq'),support_sequences=[q.get('seq') for q in supports],
                                   top_budget_kg=min(finite) if finite else None,
                                   support_budgets={str(q.get('seq')):v for q,v in zip(chain,budgets) if v is not None},
                                   confirmation_source='USER_PROJECT_DECLARATION',
                                   delivery_method='分阶段递袋或装袋辅助工具；不要求人员站入最终袋区',
                                   required_delivery_reach_mm=max(0.,frontier-x),delivery_headroom_mm=headroom,
                                   outside_work_depth_mm=max(0.,frontier+800-L)))
    return candidates
