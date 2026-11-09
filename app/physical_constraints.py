from __future__ import annotations

import math

from app.physical_inputs import source_verified, source_label, normalize_source

EPS=1e-6


def validate_contact_geometry(pkg):
    """No external spreader footprint is modeled; input must fit the package base."""
    def number(key, default=0):
        value=float(pkg.get(key) or default)
        if not math.isfinite(value) or value<0:
            raise ValueError('接触面积参数必须为有限非负数：'+key)
        return value
    l=number('l',pkg.get('length_mm') or 0)
    w=number('w',pkg.get('width_mm') or 0)
    mode=str(pkg.get('contact_area_mode') or 'UNSPECIFIED').upper().strip()
    if mode not in {'UNSPECIFIED','FULL_BASE','SKIDS','CUSTOM'}:
        raise ValueError('接触面积模式无效')
    if mode=='CUSTOM':
        area=number('custom_contact_area_m2')
        if area<=0 or area>l*w/1e6+1e-9:
            raise ValueError('有效接触面积必须大于0且不得超过包装底面积；外扩垫板需另行建模')
    if mode=='SKIDS':
        n=number('skid_count'); bw=number('skid_width_mm'); ln=number('skid_contact_length_mm')
        if n<1 or n!=int(n) or bw<=0 or ln<=0:
            raise ValueError('滑木数量必须为正整数，宽度与有效接触长度必须大于0')
        # Upright 90-degree rotation is allowed: accept either original base axis.
        if not ((ln<=l+EPS and n*bw<=w+EPS) or (ln<=w+EPS and n*bw<=l+EPS)):
            raise ValueError('滑木长度或总接触宽度超出包装底面（不允许重叠面积重复累计）')


def contact_area_details(pkg:dict):
    """Return effective static contact area and provenance.

    Modes:
      FULL_BASE   explicit full-base contact (confirmed assumption)
      SKIDS       confirmed skid geometry: count * width * effective contact length
      CUSTOM      confirmed user-supplied effective contact area
      UNSPECIFIED planning-only fallback to full footprint; never treated as a confirmed pass basis
    """
    validate_contact_geometry(pkg)
    l=max(0.0,float(pkg.get('l') or pkg.get('length_mm') or 0))
    w=max(0.0,float(pkg.get('w') or pkg.get('width_mm') or 0))
    full=l*w/1e6
    mode=str(pkg.get('contact_area_mode') or 'UNSPECIFIED').strip().upper()
    source_type=normalize_source(pkg.get('contact_area_source_type'))
    source_ok=source_verified(source_type)
    source_note=source_label(source_type)
    if mode=='CUSTOM':
        try: area=float(pkg.get('custom_contact_area_m2') or 0)
        except Exception: area=0.0
        if area>EPS:
            return {'area_m2':area,'mode':'CUSTOM','confirmed':source_ok,'source_type':source_type,'source_label':source_note,'note':'自定义有效接触面积；来源='+source_note}
    if mode=='SKIDS':
        try:
            n=max(0,int(pkg.get('skid_count') or 0)); bw=max(0.0,float(pkg.get('skid_width_mm') or 0)); ln=max(0.0,float(pkg.get('skid_contact_length_mm') or 0))
        except Exception:
            n=0; bw=ln=0.0
        area=n*bw*ln/1e6
        if area>EPS:
            return {'area_m2':area,'mode':'SKIDS','confirmed':source_ok,'source_type':source_type,'source_label':source_note,'note':f'{n}根滑木×{bw:g}mm×{ln:g}mm；来源={source_note}'}
    if mode=='FULL_BASE' and full>EPS:
        return {'area_m2':full,'mode':'FULL_BASE','confirmed':source_ok,'source_type':source_type,'source_label':source_note,'note':'整底面接触假设；来源='+source_note}
    if full>EPS:
        return {'area_m2':full,'mode':'UNSPECIFIED','confirmed':False,'source_type':'UNCONFIRMED','source_label':'未确认','note':'接触面积未确认；规划阶段暂按整底面估算'}
    return {'area_m2':None,'mode':'UNSPECIFIED','confirmed':False,'source_type':'UNCONFIRMED','source_label':'未确认','note':'无有效接触面积'}


def floor_contact_pressure_kg_m2(pkg:dict, carried_weight_kg:float=0.0):
    """Planning static average contact load; carried weight may be a conservative bound, not a structural floor/beam/point-load check."""
    if float(pkg.get('z') or 0)>EPS:
        return None
    d=contact_area_details(pkg)
    area=d.get('area_m2')
    if area is None or area<=EPS:
        return None
    return (max(0.0,float(pkg.get('weight_kg') or 0))+max(0.0,float(carried_weight_kg or 0)))/area


def _overlap_area_xy(a:dict,b:dict):
    dx=max(0.0,min(float(a['x'])+float(a['l']),float(b['x'])+float(b['l']))-max(float(a['x']),float(b['x'])))
    dy=max(0.0,min(float(a['y'])+float(a['w']),float(b['y'])+float(b['w']))-max(float(a['y']),float(b['y'])))
    return dx*dy


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _contact_hull(nodes):
    points=sorted(set(nodes))
    lower=[]; upper=[]
    for chain, seq in ((lower, points), (upper, reversed(points))):
        for pt in seq:
            while len(chain)>=2 and _cross(chain[-2],chain[-1],pt)<=0:
                chain.pop()
            chain.append(pt)
    return lower[:-1]+upper[:-1]


def _balanced_reactions(nodes, target):
    """One feasible nonnegative vertical reaction field, not a stiffness solution.

    Force and both horizontal moments balance. The resultant must lie within the
    convex hull of actual contact rectangles. Internal force distribution remains
    indeterminate; capacity checks use separate conservative ancestor bounds.
    """
    hull=_contact_hull(nodes)
    if len(hull)<3: return None
    # Normalize to keep tolerance dimensionless even for large millimetre coordinates.
    scale=max(max(x for x,y in hull)-min(x for x,y in hull),
              max(y for x,y in hull)-min(y for x,y in hull),1.0)
    for i in range(1,len(hull)-1):
        a,b,c=hull[0],hull[i],hull[i+1]
        area=_cross(a,b,c)/(scale*scale)
        if area<=1e-12: continue
        u=(_cross(target,b,c)/(scale*scale))/area
        v=(_cross(a,target,c)/(scale*scale))/area
        w=1-u-v
        if min(u,v,w)>=-1e-10:
            weights=[max(0.0,t) for t in (u,v,w)]
            total=sum(weights)
            return [(pt,t/total) for pt,t in zip((a,b,c),weights) if t>0]
    return None


def support_load_analysis(placements):
    """Check vertical static equilibrium and bound loads without area-ratio sharing.

    A feasible nodal reaction field conserves force/moments for stability screening.
    With multiple supports, each downstream support is checked against the entire
    weight of every reachable ancestor (each ancestor counted once). These bounds
    are NOT simultaneous reactions and must never be added as total shipment mass.
    """
    pls=list(placements or [])
    carried={id(p):0.0 for p in pls}
    ancestors={id(p):set() for p in pls}
    incoming={id(p):[] for p in pls}
    by_id={id(p):p for p in pls}
    hard=[]; reviews=[]
    for p in sorted(pls,key=lambda q:float(q.get('z') or 0),reverse=True):
        pid=id(p)
        carried[pid]=sum(max(0.0,float(by_id[k].get('weight_kg') or 0)) for k in ancestors[pid])
        if float(p.get('z') or 0)<=EPS: continue
        supports=[]; owners={}
        for base in pls:
            if base is p or abs(float(base.get('z') or 0)+float(base.get('h') or 0)-float(p['z']))>EPS: continue
            if _overlap_area_xy(p,base)<=EPS: continue
            x0=max(float(p['x']),float(base['x'])); x1=min(float(p['x'])+float(p['l']),float(base['x'])+float(base['l']))
            y0=max(float(p['y']),float(base['y'])); y1=min(float(p['y'])+float(p['w']),float(base['y'])+float(base['w']))
            supports.append(base)
            for pt in ((x0,y0),(x0,y1),(x1,y0),(x1,y1)):
                owners.setdefault(pt,[]).append(base)
        name=str(p.get('name') or '货物')
        if not supports:
            hard.append(name+' 无有效下层支撑'); continue
        own=max(0.0,float(p.get('weight_kg') or 0))
        def cg(axis, dim):
            raw=p.get('cg_local_'+axis)
            value=float(p[dim])/2 if raw in (None,'') else float(raw)
            if not math.isfinite(value) or value<0 or value>float(p[dim]):
                raise ValueError(name+' 重心坐标超出货物或不是有限数')
            return float(p[axis])+value
        try:
            loads=[(own,cg('x','l'),cg('y','w'))]+incoming[pid]
            total=sum(t[0] for t in loads)
            target=(sum(t[0]*t[1] for t in loads)/total,sum(t[0]*t[2] for t in loads)/total) if total>EPS else (cg('x','l'),cg('y','w'))
            reactions=_balanced_reactions(list(owners),target)
        except (ValueError,TypeError,OverflowError) as exc:
            hard.append(str(exc)); reactions=None
        if reactions is None:
            hard.append(name+' 含上层载荷的合成重心投影超出实际支撑区域，无法满足静态竖向平衡')
        else:
            for pt,fraction in reactions:
                # Shared contact corners are statically indeterminate too.
                bases=owners[pt]
                for base in bases:
                    incoming[id(base)].append((total*fraction/len(bases),pt[0],pt[1]))
        for base in supports:
            ancestors[id(base)].update(ancestors[pid]|{pid})
        if len(supports)>1:
            reviews.append(name+' 跨多个支撑：反力分配取决于接触/刚度；顶载和地板采用逐支撑保守载荷上界，需工程复核')
    return {'carried':carried,'hard_issues':list(dict.fromkeys(hard)),
            'engineering_reviews':list(dict.fromkeys(reviews)),'load_basis':'逐支撑可达上层货物全重上界（非同时反力）'}


def propagated_supported_loads(placements:list[dict]):
    """Conservative per-support ancestor-weight bounds; never area-ratio reactions."""
    return support_load_analysis(placements)['carried']


def propagated_floor_loads(placements:list[dict]):
    return propagated_supported_loads(placements)


def top_load_audit(placements:list[dict], analysis=None):
    pls=list(placements or []); analysis=analysis if analysis is not None else support_load_analysis(pls); carried=analysis['carried']; rows=[]; hard=list(analysis['hard_issues'])
    for p in pls:
        load=max(0.0,carried.get(id(p),0.0))
        if load<=EPS or str(p.get('type') or '')!='木箱': continue
        verified=bool(p.get('top_load_verified')) and p.get('max_top_load_kg') not in (None,'')
        try: limit=float(p.get('max_top_load_kg')) if verified else None
        except Exception: limit=None
        name=str(p.get('name') or '木箱')
        rows.append({'name':name,'carried_weight_kg':round(load,1),'limit_kg':limit,'verified':bool(verified)})
        if not verified: hard.append(f'{name} 承受上层静载 {load:.1f} kg，但没有项目确认顶载值')
        elif load>limit+EPS: hard.append(f'{name} 累计上层静载 {load:.1f} kg > 项目确认顶载 {limit:.1f} kg')
    return {'rows':rows,'hard_issues':hard,'checked_count':len(rows),
            'engineering_reviews':analysis['engineering_reviews'],'load_basis':analysis['load_basis']}


def floor_load_audit(placements:list[dict], container:dict, analysis=None):
    """Audit static average contact pressure using explicit effective contact-area semantics."""
    raw=container.get('floor_load_kg_m2')
    try: limit=float(raw) if raw not in (None,'') else None
    except Exception: limit=None
    if limit is not None and limit<=0: limit=None
    carried=analysis['carried'] if analysis is not None else propagated_floor_loads(placements or [])
    rows=[]; hard=[]; reviews=[]; info=[]
    for p in placements or []:
        if float(p.get('z') or 0)>EPS: continue
        try:
            d=contact_area_details(p); area=d.get('area_m2')
            pressure=floor_contact_pressure_kg_m2(p,carried.get(id(p),0.0))
        except (ValueError,TypeError,OverflowError) as exc:
            hard.append(str(p.get('name') or '货物')+' 接触面积无效：'+str(exc)); continue
        if pressure is None: continue
        row={'name':str(p.get('name') or p.get('source_name') or '货物'),'pressure_kg_m2':round(pressure,1),
             'self_weight_kg':round(float(p.get('weight_kg') or 0),1),'carried_weight_kg':round(carried.get(id(p),0.0),1),
             'contact_area_m2':round(float(area or 0),4),'contact_area_mode':d.get('mode'),'contact_area_confirmed':bool(d.get('confirmed')),'contact_area_note':d.get('note')}
        rows.append(row)
        if limit is None:
            continue
        if pressure>limit+EPS:
            # Even the optimistic full-base fallback exceeds the limit -> definitely not acceptable.
            hard.append(f"{row['name']} 静态平均接触压力 {pressure:.0f} kg/m² > 项目限值 {limit:.0f} kg/m²")
        elif not d.get('confirmed'):
            reviews.append(f"{row['name']} 未确认实际接触面积；暂按整底面估算 {pressure:.0f} kg/m²，不能据此判定地板/滑木承压通过")
    if limit is None and rows:
        info.append('未配置项目静态平均接触压力限值；仅计算规划估算值，不执行承压通过判定')
    return {'checked':limit is not None,'limit_kg_m2':limit,'rows':rows,'hard_issues':hard,'engineering_reviews':list(dict.fromkeys(reviews)),
            'information_notes':info,'max_pressure_kg_m2':round(max([r['pressure_kg_m2'] for r in rows] or [0]),1)}


def door_maneuver_audit(placements:list[dict], container:dict):
    """Static geometry coverage indicator only; not forklift/rigging motion planning."""
    if container.get('vehicle_type')=='truck' and container.get('body_type')=='flatbed':
        return {'checked':False,'straight_count':0,'lift_count':0,'maneuver_count':0,'engineering_reviews':[],'review_notes':[],'information_notes':['平板货车：无箱门限制，吊装/侧装路径需按现场条件确认'],'rows':[]}
    if bool(container.get('open_top')) or 'OT' in str(container.get('code') or ''):
        return {'checked':False,'straight_count':0,'lift_count':0,'maneuver_count':0,'engineering_reviews':[],'review_notes':[],'information_notes':['OT/open-top：未执行后门直线入柜路径筛查'],'rows':[]}
    W=float(container.get('inner_w') or 0); dw=float(container.get('door_w') or W); dh=float(container.get('door_h') or container.get('inner_h') or 0)
    y0=max(0.0,(W-dw)/2.0); y1=min(W,y0+dw)
    rows=[]; review=[]; info=[]; straight=lift=maneuver=0
    for p in placements or []:
        y=float(p.get('y') or 0); z=float(p.get('z') or 0); w=float(p.get('w') or 0); h=float(p.get('h') or 0)
        lateral=(y>=y0-EPS and y+w<=y1+EPS); dimension_pass=(w<=dw+EPS and h<=dh+EPS)
        if lateral and dimension_pass and z<=EPS:
            mode='直线入柜可达（静态几何）'; straight+=1
        elif lateral and dimension_pass:
            mode='直线入柜后需举升/堆叠就位'; lift+=1; info.append(f"{p.get('name') or '货物'} 最终位于上层，需要举升/堆叠就位（信息提示，非运动仿真）")
        else:
            mode='需横移/转运/特殊装载顺序复核'; maneuver+=1; review.append(f"{p.get('name') or '货物'} 最终横向位置不在保守门洞直线投影内，需人工复核叉车/吊装/横移步骤")
        rows.append({'name':str(p.get('name') or '货物'),'mode':mode})
    return {'checked':True,'straight_count':straight,'lift_count':lift,'maneuver_count':maneuver,
            'engineering_reviews':list(dict.fromkeys(review)),'review_notes':list(dict.fromkeys(review)),'information_notes':list(dict.fromkeys(info)),'rows':rows}


def lashing_audit(placements:list[dict]):
    """Coverage check only; no lashing-force calculation."""
    review=[]; required=0; confirmed=0
    for p in placements or []:
        if not bool(p.get('lashing_required')): continue
        required+=1
        if bool(p.get('lashing_confirmed')) and int(p.get('lashing_points') or 0)>0: confirmed+=1
        else: review.append(f"{p.get('name') or '货物'} 已标记需系固，但系固点/方案尚未确认")
    return {'required_count':required,'confirmed_count':confirmed,'engineering_reviews':review,'review_notes':review,'information_notes':[]}
