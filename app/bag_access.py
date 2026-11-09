"""Staged straight-door access for bags; dimensions are planning assumptions.

Floor rectangles use conservative cargo envelopes. Before filling each region,
all equipment whose front face precedes its rear edge is installed. Future
cargo starts behind this edge, so subsequent axial insertion cannot intersect
filled bags. A standing/carrying corridor to the rear door is checked against
installed cargo and earlier bag regions. This is not forklift/lashing approval.
"""
import math
from app.physical_inputs import source_verified

MIN_WORKING_DEPTH_MM=800.0
WORKER_HEIGHT_MM=1800.0
HAND_HEIGHT_MM=1400.0
HAND_REACH_MM=600.0
EPS=1e-6


def overlap(a,b):
    return all(min(a[k]+a[d],b[k]+b[d])-max(a[k],b[k])>EPS for k,d in zip(('x','y','z'),('l','w','h')))


def as_dict(space):return dict(zip(('x','y','z','l','w','h'),space))


def floor_rectangles(box,min_side=800.):
    """Disjoint largest rectangles on an exact cargo-edge grid, no discretization."""
    L,W=float(box['inner_l']),float(box['inner_w'])
    qs=box.get('placements',[])
    xs=sorted({0.,L}|{max(0.,min(L,float(q['x'])+s*float(q['l']))) for q in qs for s in (0,1)})
    ys=sorted({0.,W}|{max(0.,min(W,float(q['y'])+s*float(q['w']))) for q in qs for s in (0,1)})
    nx,ny=len(xs)-1,len(ys)-1
    free=[[not any(q['x']<xs[i+1]-EPS and q['x']+q['l']>xs[i]+EPS and q['y']<ys[j+1]-EPS and q['y']+q['w']>ys[j]+EPS for q in qs) for j in range(ny)] for i in range(nx)]
    out=[]
    while True:
        best=None;area=0.
        for a in range(nx):
            good=[True]*ny
            for b in range(a,nx):
                good=[v and free[b][j] for j,v in enumerate(good)]
                if xs[b+1]-xs[a]<min_side-EPS:continue
                j=0
                while j<ny:
                    if not good[j]:j+=1;continue
                    c=j
                    while j<ny and good[j]:j+=1
                    w=ys[j]-ys[c];size=(xs[b+1]-xs[a])*w
                    if w>=min_side-EPS and size>area+EPS:area=size;best=(a,b+1,c,j)
        if best is None:break
        a,b,c,d=best
        out.append((xs[a],ys[c],0.,xs[b]-xs[a],ys[d]-ys[c],float(box['inner_h'])))
        for i in range(a,b):
            for j in range(c,d):free[i][j]=False
    return out


def _stage(box,space,kind='floor',support=None,work=None):
    x,y,z,l,w,h=space;frontier=x+l
    installed=[q for q in box.get('placements',[]) if q['x']<frontier-EPS]
    # Install physical supports before any elevated equipment in this stage.
    changed=True
    while changed:
        changed=False
        for upper in list(installed):
            for lower in box.get('placements',[]):
                if lower in installed or abs(lower['z']+lower['h']-upper['z'])>EPS:continue
                if min(lower['x']+lower['l'],upper['x']+upper['l'])>max(lower['x'],upper['x'])+EPS and min(lower['y']+lower['w'],upper['y']+upper['w'])>max(lower['y'],upper['y'])+EPS:
                    installed.append(lower);changed=True
    installed.sort(key=lambda q:(q['z'],q['x'],q['y']))
    # A straight 800mm-wide corridor is also an explicit construction for the
    # door-connected route; no blind flood-fill through narrow corners.
    opening_left=max(0.,(float(box['inner_w'])-float(box.get('door_w',box['inner_w'])))/2)
    opening_right=float(box['inner_w'])-opening_left
    if work is None:
        lo=max(y,opening_left);hi=min(y+w,opening_right)-MIN_WORKING_DEPTH_MM
        if hi<lo-EPS:return None
        wy=max(lo,min(hi,y+(w-MIN_WORKING_DEPTH_MM)/2))
    else:
        wy=work['y']
        if wy<opening_left-EPS or wy+MIN_WORKING_DEPTH_MM>opening_right+EPS:return None
    wx=x if work is None else work['x']
    corridor=dict(x=wx,y=wy,z=0.,l=float(box['inner_l'])-wx,w=MIN_WORKING_DEPTH_MM,h=WORKER_HEIGHT_MM)
    if corridor['y']<-EPS or corridor['y']+corridor['w']>box['inner_w']+EPS:return None
    if any(overlap(corridor,q) for q in installed):return None
    return dict(space=space,kind=kind,frontier_mm=frontier,corridor=corridor,
                installed_sequences=[q.get('seq',i+1) for i,q in enumerate(installed)],
                future_sequences=[q.get('seq',i+1) for i,q in enumerate(box.get('placements',[])) if q not in installed],
                support_sequence=None if support is None else support.get('seq'),
                top_budget_kg=None if support is None else support['_top_remaining'],
                work_area=work)


def plan_access(box,reference_dimensions=(560,560,800)):
    if float(box.get('door_w',box['inner_w']))<800 or float(box.get('door_h',box['inner_h']))<WORKER_HEIGHT_MM or float(box['inner_h'])<WORKER_HEIGHT_MM:return []
    min_side=max(MIN_WORKING_DEPTH_MM,max(reference_dimensions))
    candidates=[]
    for rect in floor_rectangles(box,min_side):
        step=_stage(box,rect)
        if step:candidates.append(step)
    # Only verified flat wooden crates on the floor can support manual top
    # filling. Keep 600mm reach strips and a separate 800mm standing area.
    # Each crate gets one top strip so its declared remaining budget is never
    # double counted; above-cargo mass is conservatively charged in full.
    qs=box.get('placements',[])
    for q in qs:
        cap=q.get('max_top_load_kg')
        if q.get('type')!='木箱' or abs(float(q['z']))>EPS or not q.get('top_load_verified') or not source_verified(q.get('top_load_source_type')):continue
        if cap is None or not math.isfinite(float(cap)) or float(cap)<=0:continue
        z=q['z']+q['h'];height=min(float(box['inner_h']),HAND_HEIGHT_MM)-z
        if height<min(reference_dimensions)-EPS or q['l']<min_side-EPS or q['w']<min(reference_dimensions)-EPS:continue
        above=[r for r in qs if r is not q and r['z']>=z-EPS and min(q['x']+q['l'],r['x']+r['l'])>max(q['x'],r['x'])+EPS and min(q['y']+q['w'],r['y']+r['w'])>max(q['y'],r['y'])+EPS]
        remaining=float(cap)-sum(float(r.get('weight_kg',0)) for r in above)
        if remaining<=0:continue
        width=min(float(q['w']),HAND_REACH_MM)
        for side in (-1,1):
            y=q['y'] if side<0 else q['y']+q['w']-width
            space=(q['x'],y,z,q['l'],width,height)
            work=dict(x=q['x'],y=q['y']-800 if side<0 else q['y']+q['w'],z=0.,l=q['l'],w=800.,h=WORKER_HEIGHT_MM)
            if work['y']<0 or work['y']+work['w']>box['inner_w']:continue
            if any(overlap(work,r) for r in qs) or any(overlap(as_dict(space),r) for r in qs):continue
            support=dict(q,_top_remaining=remaining);step=_stage(box,space,'verified_crate_top',support,work)
            if step:candidates.append(step);break
    candidates.sort(key=lambda a:(a['frontier_mm'],a['kind']=='floor',a['space'][0],a['space'][1]))
    # Previously filled areas must leave each new corridor and standing area open.
    accepted=[]
    for step in candidates:
        if any(overlap(as_dict(old['space']),step['corridor']) or (step['work_area'] and overlap(as_dict(old['space']),step['work_area'])) for old in accepted):continue
        accepted.append(step)
    return accepted


def eligible_zones(box,reference_dimensions=(560,560,800)):
    return [p['space'] for p in plan_access(box,reference_dimensions)]


def audit(box,eligible,geometric_free_m3,steps=None):
    capacity=sum(q[3]*q[4]*q[5] for q in eligible)/1e9
    pending=sum(q.get('type')=='木箱' and not (q.get('top_load_verified') and source_verified(q.get('top_load_source_type'))) for q in box.get('placements',[]))
    return dict(policy='staged_floor_and_verified_tops',door='x=L',working_depth_mm=800.,worker_height_mm=1800.,hand_height_mm=1400.,hand_reach_mm=600.,
                eligible_volume_m3=round(capacity,9),excluded_void_volume_m3=round(max(0.,geometric_free_m3-capacity),9),unconfirmed_top_load_crates=pending,
                sequence='从柜头向柜门分阶段装设备、完成固定后装袋；先填较深区域，每一步保留800mm宽、1800mm高直通柜门的作业路径，随后设备不得穿越已填区域。',
                status='操作尺寸为规划假设；设备进柜/支撑系固、袋装堆高承压、挡护及现场工法待确认',loading_steps=[])


# Keep the previous verified-top/floor policy available when no project-specific
# declaration applies. The new branch is strictly scoped to declared cargo.
_base_access=plan_access


def _schedule(box,candidates):
    selected=[];frontier=0.
    for original in candidates:
        s=dict(original);frontier=max(frontier,s['frontier_mm'])
        installed=[q for q in box.get('placements',[]) if q['x']<frontier-EPS]
        if any(overlap(s['corridor'],q) or (s.get('work_area') and overlap(s['work_area'],q)) for q in installed):continue
        if any(overlap(as_dict(p['space']),as_dict(s['space'])) for p in selected):continue
        if any(overlap(as_dict(p['space']),s['corridor']) or (s.get('work_area') and overlap(as_dict(p['space']),s['work_area'])) for p in selected):continue
        if s['kind']=='user_confirmed_top':
            x,y,z,l,w,h=s['space'];route=dict(x=x,y=y,z=float(box['inner_h'])-s['delivery_headroom_mm'],l=max(0.,s['frontier_mm']-x),w=w,h=s['delivery_headroom_mm'])
            if any(overlap(as_dict(p['space']),route) for p in selected):continue
            s['delivery_route']=route
        s['frontier_mm']=frontier
        installed.sort(key=lambda q:(q['z'],q['x'],q['y']))
        s['installed_sequences']=[q.get('seq',i+1) for i,q in enumerate(installed)]
        s['future_sequences']=[q.get('seq',i+1) for i,q in enumerate(box.get('placements',[])) if q not in installed]
        selected.append(s)
    return selected


def plan_access(box,reference_dimensions=(560,560,800),policy=None):
    base=_base_access(box,reference_dimensions)
    if not policy:return base
    from app.bag_top_access import top_stages
    tops=top_stages(box,policy,reference_dimensions)
    def rank(s):return (s['frontier_mm'],s['space'][0],s['kind']=='floor',s['space'][1])
    # Compare two loading orders rather than filling a floor pocket that blocks
    # all subsequent delivery into a larger top region.
    a=_schedule(box,sorted(base+tops,key=rank))
    b=_schedule(box,sorted(tops,key=rank)+sorted(base,key=rank))
    def score(steps):
        return sum(min(s['space'][3]*s['space'][4]*s['space'][5]/1e9,(s['top_budget_kg']/56 if s['top_budget_kg'] is not None else float('inf'))) for s in steps)
    return max((a,b),key=score)


_base_audit=audit

def audit(box,eligible,geometric_free_m3,steps=None,policy=None):
    a=_base_audit(box,eligible,geometric_free_m3,steps)
    if policy:
        from app.bag_top_policy import approved
        a.update(policy='project_confirmed_top_and_staged_floor',confirmation_source='USER_PROJECT_DECLARATION',confirmation_note=policy.get('statement',''),
                 approved_top_packages=sum(approved(q,policy) for q in box.get('placements',[])),
                 unconfirmed_top_load_crates=sum(q.get('type')=='木箱' and not approved(q,policy) and not(q.get('top_load_verified') and source_verified(q.get('top_load_source_type'))) for q in box.get('placements',[])),technical_top_load_certified=False,
                 sequence='按阶段完成设备和固定，先比较顶部递袋与落地填装顺序；保持工作面及柜门通道，避免后续设备穿越已填袋区。顶部通过递袋或辅助工具送达，不要求人员站入袋区。',
                 status='顶部放袋依据本项目用户声明；所需送袋距离/净空及辅助工法会逐步列出，未进行厂家顶载或逐包输送几何认证。')
    return a
