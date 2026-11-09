"""Bounded joint ranking: equipment risk first, then total bags+equipment bins."""
import copy,math
from app.bag_access import plan_access


def capacity(plan,c,bags,policy):
    unit=float(bags[0]['flexible_unit_volume_m3']);weight=float(bags[0]['weight_kg']);ref=[bags[0][k] for k in ('length_mm','width_mm','height_mm')]
    total=0
    for b in plan['containers']:
        probe=dict(b,door_w=c.get('door_w',b['inner_w']),door_h=c.get('door_h',b['inner_h']))
        used={};n=0
        for s in plan_access(probe,ref,policy):
            count=int(math.floor((s['space'][3]*s['space'][4]*s['space'][5]/1e9+1e-9)/unit))
            budgets=s.get('support_budgets',{})
            if not budgets and s['top_budget_kg'] is not None:budgets={str(s['support_sequence']):s['top_budget_kg']}
            if weight>0 and budgets:count=min(count,max(0,int(min(v-used.get(k,0) for k,v in budgets.items())/weight+1e-9)))
            n+=count
            for k in budgets:used[k]=used.get(k,0)+count*weight
        if weight>0:n=min(n,max(0,int((b['payload']-b['total_weight'])/weight+1e-9)))
        total+=n
    return min(len(bags),total)


def choose_equipment_plan(m,normal,bags,c,original,policy):
    if len({(float(b['weight_kg']),float(b['flexible_unit_volume_m3'])) for b in bags})!=1:return original
    unit=float(bags[0]['flexible_unit_volume_m3']);weight=float(bags[0]['weight_kg'])
    empty=int((c['inner_l']*c['inner_w']*c['inner_h']/1e9+1e-9)/unit)
    if weight>0:empty=min(empty,int(c['payload']/weight))
    if empty<=0:return original
    ranked=[]
    def record(p,label):
        n=capacity(p,c,bags,policy);predicted=p['count']+math.ceil(max(0,len(bags)-n)/empty)
        key=(p['engineering_fail_count'],p['engineering_high_risk_count'],predicted,-n,p['count'])
        ranked.append((key,p,label,n,predicted))
    record(original,'原设备布局')
    # Deterministic bounded search: at most two alternative orderings.
    variants=[sorted(normal,key=lambda p:(p['height_mm'],p['length_mm']*p['width_mm'],p['weight_kg']),reverse=True),sorted(normal,key=lambda p:(p['length_mm']*p['width_mm'],p['weight_kg']),reverse=True)]
    for index,ordered in enumerate(variants,1):
        states=[m._new_bin_state(c) for _ in range(original['count'])];ok=True
        for pkg in ordered:
            if not m._place_one(pkg,states):ok=False;break
        if not ok:continue
        states=[s for s in states if s['placements']]
        alt=m._decorate_plan(states,c['code'],c['code'])
        if any(m._container_physical_issues(b) for b in alt['containers']):continue
        record(alt,'联合候选'+str(index))
    best=min(ranked,key=lambda r:r[0]);out=copy.deepcopy(best[1])
    out['bag_joint_optimization']=dict(candidates_evaluated=len(ranked),selected=best[2],predicted_total_containers=best[4],bags_in_equipment_capacity=best[3],equipment_geometry_unchanged=best[2]=='原设备布局',baseline_predicted_containers=ranked[0][4],baseline_bags_capacity=ranked[0][3],search='有限候选；先比较设备硬失败/高风险，再比较包含珠光砂的总柜数和设备柜装袋容量；不保证全局最优')
    return out
