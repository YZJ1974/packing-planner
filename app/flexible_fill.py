"""Declared flexible-bag volume allocation. Not a per-bag geometric certificate."""
import math,copy
from app.bag_display import patches
from app.bag_top_policy import applicable,approved
from app.bag_access import plan_access, audit as access_audit

def subtract(space,q):
    x,y,z,l,w,h=space;a,b,c=q['x'],q['y'],q['z'];d,e,f=a+q['l'],b+q['w'],c+q['h']
    x0,y0,z0=max(x,a),max(y,b),max(z,c);x1,y1,z1=min(x+l,d),min(y+w,e),min(z+h,f)
    if min(x1-x0,y1-y0,z1-z0)<=1e-9:return [space]
    boxes=[(x,y,z,x0-x,w,h),(x1,y,z,x+l-x1,w,h),(x0,y,z,x1-x0,y0-y,h),(x0,y1,z,x1-x0,y+w-y1,h),(x0,y0,z,x1-x0,y1-y0,z0-z),(x0,y0,z1,x1-x0,y1-y0,z+h-z1)]
    return [b for b in boxes if min(b[3:])>1e-9]

def voids(box):
    free=[(0.,0.,0.,float(box['inner_l']),float(box['inner_w']),float(box['inner_h']))]
    for q in box.get('placements',[]):free=[p for b in free for p in subtract(b,q)]
    return sorted(free,key=lambda b:(b[2],b[0],b[1]))

def pack(m,packages,c):
    # This mode is deliberately separate from truck axle and top-load geometry.
    if c.get('vehicle_type')=='truck':raise ValueError('柔性袋装体积分配目前仅用于集装箱，请切回集装箱规划')
    normal=[p for p in packages if not p.get('flexible_unit_volume_m3')];bags=[p for p in packages if p.get('flexible_unit_volume_m3')]
    if not bags:return None
    policy=applicable(m._bag_fill_policy() if hasattr(m,'_bag_fill_policy') else {},bags)
    if policy and not any(approved(p,policy) for p in normal):policy={}
    # Reuse equipment geometry solver and all its constraints, independent of bag volume mode.
    original=m._homogeneous_plan_fast(normal,c) if normal else m._decorate_plan([],c['code'],c['code'])
    if original is None:return None
    # Prefer an equipment arrangement with no high-risk CG before adding unlocated flexible loads.
    if original['engineering_fail_count'] or original['engineering_high_risk_count']:
        for count in (original['count'],original['count']+1):
            states=m.pack_specific_bins_multistart(normal,[dict(c) for _ in range(count)])
            if states:
                alt=m._decorate_plan(states,c['code'],c['code'])
                if (alt['engineering_fail_count'],alt['engineering_high_risk_count'])<(original['engineering_fail_count'],original['engineering_high_risk_count']):original=alt
            if not original['engineering_fail_count'] and not original['engineering_high_risk_count']:break
    if policy and normal:
        from app.bag_joint import choose_equipment_plan
        original=choose_equipment_plan(m,normal,bags,c,original,policy)
    plan=copy.deepcopy(original);plan.pop('compression_audit',None);boxes=plan['containers'];base_count=len(boxes);i=0;in_existing=0
    while i<len(bags):
        if i>=0 and not boxes:boxes.append(m._decorate_plan([m._new_bin_state(c)],c['code'],c['code'])['containers'][0])
        start=i
        for box in boxes:
            if i>=len(bags):break
            if box.get('_volume_visited'):continue
            box['_volume_visited']=True
            geometric=voids(box);geometric_m3=sum(b[3]*b[4]*b[5] for b in geometric)/1e9
            box['door_w']=c.get('door_w',box['inner_w']);box['door_h']=c.get('door_h',box['inner_h'])
            ref=[float(bags[i][k]) for k in ('length_mm','width_mm','height_mm')]
            access=plan_access(box,ref,policy);free=[s['space'] for s in access];free_m3=sum(b[3]*b[4]*b[5] for b in free)/1e9
            box['bag_access_audit']=access_audit(box,free,geometric_m3,access,policy)
            remaining_weight=float(box['payload'])-float(box['total_weight']);chosen=[];vol=mass=0.;zones=[];used_top_load={}
            for stage in access:
                if i>=len(bags):break
                x,y,z,l,w,h=stage['space'];capacity=l*w*h/1e9
                stage_mass=stage_volume=0.;stage_count=0
                support_budgets=stage.get('support_budgets',{})
                if not support_budgets and stage['top_budget_kg'] is not None:support_budgets={str(stage['support_sequence']):stage['top_budget_kg']}
                remaining_budgets={key:value-used_top_load.get(key,0.) for key,value in support_budgets.items()}
                budget=min(remaining_budgets.values()) if remaining_budgets else None
                while i<len(bags):
                    pkg=bags[i];unit=float(pkg['flexible_unit_volume_m3']);weight=float(pkg['weight_kg'])
                    if unit<=0 or not math.isfinite(unit) or weight<0 or not math.isfinite(weight):raise ValueError('袋装体积或重量无效')
                    if stage_volume+unit>capacity+1e-9 or mass+weight>remaining_weight+1e-6:break
                    if budget is not None and stage_mass+weight>budget+1e-6:break
                    chosen.append(pkg);i+=1;stage_count+=1;stage_volume+=unit;stage_mass+=weight;vol+=unit;mass+=weight
                if not stage_count:continue
                zone=dict(x=x,y=y,z=z,l=l,w=w,h=stage_volume*1e9/(l*w),access_kind=stage['kind'],support_sequence=stage['support_sequence'],loading_order=len(zones)+1,quantity=stage_count)
                for key in support_budgets:used_top_load[key]=used_top_load.get(key,0.)+stage_mass
                zones.append(zone)
                box['bag_access_audit']['loading_steps'].append(dict(order=len(zones),kind=stage['kind'],frontier_mm=stage['frontier_mm'],install_before=stage['installed_sequences'],install_later=stage['future_sequences'],corridor=stage['corridor'],work_area=stage['work_area'],zone=zone,quantity=stage_count,volume_m3=round(stage_volume,9),weight_kg=stage_mass,top_budget_kg=budget,support_sequence=stage['support_sequence']))
            for index,step in enumerate(box['bag_access_audit']['loading_steps']):
                # Access proof and delivery requirements refer to the selected
                # stage, not to a claimed per-bag coordinate certificate.
                stage=next(s for s in access if s['space'][:3]==tuple(step['zone'][k] for k in ('x','y','z')) and abs(s['space'][3]-step['zone']['l'])<1e-6 and abs(s['space'][4]-step['zone']['w'])<1e-6)
                for key in ('support_sequences','confirmation_source','delivery_method','required_delivery_reach_mm','delivery_headroom_mm','outside_work_depth_mm','delivery_route'):
                    if key in stage:step[key]=stage[key]
            if not chosen:continue
            if int(box['index'])<=base_count:in_existing+=len(chosen)
            allocation=dict(name='珠光砂（柔性袋装体积分配）',quantity=len(chosen),volume_m3=round(vol,9),weight_kg=round(mass,6),names=[p['name'] for p in chosen],asu_key=chosen[0].get('asu_key'),unit_volume_m3=chosen[0]['flexible_unit_volume_m3'],reference_dimensions_mm=[chosen[0]['length_mm'],chosen[0]['width_mm'],chosen[0]['height_mm']])
            box['flexible_fill']=[allocation];box['flexible_zones']=zones;box['bag_display_patches']=patches(zones);box['total_weight']=round(box['total_weight']+mass,6);cap=box['inner_l']*box['inner_w']*box['inner_h']
            used=cap-geometric_m3*1e9+vol*1e9
            box['planning_occupied_volume_mm3']=used
            assert used<=cap+1
            box['volume_utilization']=round(used/cap*100,1);box['weight_utilization']=round(box['total_weight']/box['payload']*100,1)
            a=box['engineering_audit'];note='袋装采用分阶段可达区：每一步检查800mm宽/1800mm高直通柜门的作业路径，后装设备不得穿越已填区；只允许已确认顶载的低位平顶木箱承放。操作尺寸为规划假设；设备进柜系固、袋装堆高承压/工法、挡护及整柜重心待确认。'
            if policy:note='顶部放袋按本项目用户确认条件，包含木箱、圆筒、桥架和平台；仅用于此批设备及声明的袋装密度，未换成厂家顶载限值。采用分阶段递袋/辅助送袋与落地区联合规划，逐步列明工作面、通道、送袋距离和净空；不作逐包输送几何或发运认证。'
            a['engineering_review_notes'].append(note);a['note']+='；'+note;a['flexible_volume_only']=True;a['cg_scope']='仅已定位设备/木箱，未包含柔性袋装';a['floor_load_checked']=False
            if a['status']=='通过':a['status']='需复核'
        if i==len(bags):break
        box=m._decorate_plan([m._new_bin_state(c)],c['code'],c['code'])['containers'][0];box['index']=len(boxes)+1;boxes.append(box)
        if i==start and len(boxes)>base_count+2 and not boxes[-2].get('flexible_fill'):return None
    for box in boxes:box.pop('_volume_visited',None)
    plan.update(count=len(boxes),package_count=sum(len(b['placements'])+sum(a['quantity'] for a in b.get('flexible_fill',[])) for b in boxes),flexible_volume_only=True)
    plan['engineering_review_count']=sum(b['engineering_audit']['status']!='通过' for b in boxes)
    plan['void_fill_audit']={'bags_in_existing_voids':in_existing,'flexible_volume_only':True,'access_policy':'project_confirmed_top_and_staged_floor' if policy else 'staged_floor_and_verified_tops','excluded_void_volume_m3':round(sum(b.get('bag_access_audit',{}).get('excluded_void_volume_m3',0) for b in boxes),6),'total_bags':len(bags),'total_volume_m3':round(sum(float(p['flexible_unit_volume_m3']) for p in bags),6),'note':'分阶段装袋规划：柜头、设备间和尾部的可达落地区优先；各步保留直通柜门的作业路径，已确认顶载且可从旁侧操作的低位木箱允许限载填装。未知顶载、窄缝和封闭区排除；按每立方8包估算，堆高承压与现场工法待确认。'}
    if policy:
        plan['void_fill_audit'].update(confirmation_source='USER_PROJECT_DECLARATION',confirmation_note=policy.get('statement',''),note='已按本项目用户确认允许设备顶部放珠光砂；比较设备摆放与分阶段装袋顺序。顶部递袋/辅助工具所需距离与净空见作业步骤；窄缝不作为人员通道。体积、重量、柜界与已知限载保持约束，仍为规划估算。')
    return plan
