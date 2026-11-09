"""Editable truck inputs and a declared two-effective-support static model.

Templates are dimensional examples, never certified vehicle ratings or road limits.
"""
import json,math,copy,datetime,io,zipfile
from xml.etree import ElementTree as ET
from urllib.parse import quote
from fastapi import Request
from fastapi.responses import RedirectResponse,JSONResponse
from app.input_validation import number
from app.asu_standard import digest

PRESETS=[('TRUCK_6M8','6.8米平板货车',6800,2400,2500),('TRUCK_13M6','13.6米平板半挂车',13600,2500,2700),('TRUCK_17M5','17.5米低平板货车',17500,3000,3000),('TRUCK_CUSTOM','自定义货车',6800,2400,2500)]
FIELDS=('length_mm','width_mm','cargo_height_mm','payload_kg','door_width_mm','door_height_mm','clearance_mm','deck_height_mm','empty_height_mm','route_height_mm','floor_limit_kg_m2','support_a_x_mm','support_b_x_mm','empty_a_kg','empty_b_kg','limit_a_kg','limit_b_kg','max_gross_kg','empty_mass_kg')
AXLE=('support_a_x_mm','support_b_x_mm','empty_a_kg','empty_b_kg','limit_a_kg','limit_b_kg')
def defaults():
    return {'mode':'container','vehicles':[dict(code=c,label=n,enabled=False,body_type='flatbed',length_mm=l,width_mm=w,cargo_height_mm=h,payload_kg=None,door_width_mm=w,door_height_mm=h,clearance_mm=0,**{k:None for k in FIELDS if k not in ('length_mm','width_mm','cargo_height_mm','payload_kg','door_width_mm','door_height_mm','clearance_mm')},reference='',evidence_id=None) for c,n,l,w,h in PRESETS]}
def validate(row):
    d=copy.deepcopy(row)
    if d.get('code') not in {p[0] for p in PRESETS}:raise ValueError('车型代码无效')
    if d.get('body_type') not in ('flatbed','box'):raise ValueError('车身类型无效')
    if not isinstance(d.get('enabled'),bool):raise ValueError('车型启用状态无效')
    for k in FIELDS:
        value=d.get(k)
        if value in (None,''):d[k]=None;continue
        if k in ('support_a_x_mm','support_b_x_mm'):
            value=float(value)
            if not math.isfinite(value):raise ValueError('支承坐标必须是有限数值')
            d[k]=value
        else:d[k]=number(value,k)
    if d['enabled']:
        for k in ('length_mm','width_mm','cargo_height_mm','payload_kg'):
            if not d[k] or d[k]<=0:raise ValueError('启用车型前必须填写正数：'+k)
        if 2*(d.get('clearance_mm') or 0)>=min(d['length_mm'],d['width_mm']):raise ValueError('操作间隙占满货台')
        if d['body_type']=='box':
            if not d.get('door_width_mm') or not d.get('door_height_mm'):raise ValueError('厢式货车必须填写柜门/车门宽高')
        if d.get('floor_limit_kg_m2') is not None and d['floor_limit_kg_m2']<=0:raise ValueError('货台接触压力限值须大于0或留空')
        for k in ('deck_height_mm','empty_height_mm'):
            if d.get(k) is not None and d[k]<=0:raise ValueError('实车高度必须大于0或留空')
        if d.get('deck_height_mm') is not None and d.get('empty_height_mm') is not None and d['empty_height_mm']<d['deck_height_mm']:raise ValueError('空车自身高度不能低于货台高度')
        vals=[d.get(k) for k in AXLE]
        if any(v is not None for v in vals):
            if not all(v is not None for v in vals):raise ValueError('两支承组模型的坐标、空载载荷及允许载荷必须完整填写，或全部留空')
            if d['support_b_x_mm']<=d['support_a_x_mm']:raise ValueError('B组坐标必须大于A组坐标')
            if min(d['limit_a_kg'],d['limit_b_kg'])<=0:raise ValueError('轴/支承组允许载荷须大于0')
            if d['empty_a_kg']>d['limit_a_kg'] or d['empty_b_kg']>d['limit_b_kg']:raise ValueError('空载支承组载荷已超限')
            if d['empty_a_kg']+d['empty_b_kg']<=0:raise ValueError('模型范围空载总质量须大于0')
        if d.get('route_height_mm') is not None:
            if d.get('deck_height_mm') is None:raise ValueError('核对路线高度前须填写货台离地高度')
            if d['route_height_mm']<=d['deck_height_mm']:raise ValueError('路线总高度必须高于货台')
            if d.get('empty_height_mm') and d['empty_height_mm']>d['route_height_mm']:raise ValueError('空车自身高度已超过填写的路线高度')
        if d.get('max_gross_kg') is not None and d['max_gross_kg']<=0:raise ValueError('车辆总质量上限须大于0')
        if all(d.get(k) is not None for k in AXLE) and d.get('max_gross_kg') is not None and d['max_gross_kg']<=d['empty_a_kg']+d['empty_b_kg']:raise ValueError('模型范围总质量上限必须高于空载质量')
    if d['enabled']:
        if d.get('empty_mass_kg') is not None and d['empty_mass_kg']<=0:raise ValueError('empty_mass_kg：空载总质量须大于0或留空')
        if all(d.get(k) is not None for k in AXLE) and d.get('empty_mass_kg') is not None and abs(d['empty_mass_kg']-d['empty_a_kg']-d['empty_b_kg'])>1e-6:raise ValueError('empty_mass_kg：空载总质量与A/B组空载质量之和不一致')
        if d.get('max_gross_kg') is not None and empty_mass(d) is not None and d['max_gross_kg']<=empty_mass(d):raise ValueError('max_gross_kg：总质量上限必须高于空载总质量')
    d['label']=str(d.get('label') or d['code'])[:100];d['reference']=str(d.get('reference') or '')[:1000]
    return d
def parameters(row):return {k:row.get(k) for k in ('code','label','body_type','reference',*FIELDS)}
def empty_mass(row):
    if all(row.get(k) is not None for k in AXLE):return row['empty_a_kg']+row['empty_b_kg']
    return row.get('empty_mass_kg')
def as_carrier(row):
    r=validate(row);h=r['cargo_height_mm']
    if r.get('route_height_mm') is not None:h=min(h,r['route_height_mm']-r['deck_height_mm'])
    payload=r['payload_kg']
    if all(r.get(k) is not None for k in AXLE):
        empty=r['empty_a_kg']+r['empty_b_kg']
        payload=min(payload,r['limit_a_kg']+r['limit_b_kg']-empty)
    if r.get('max_gross_kg') is not None:
        payload=min(payload,r['max_gross_kg']-(empty_mass(r) or 0))
    if payload<=0:raise ValueError('空载及总质量/支承组约束后没有可装载余量')
    return {'code':r['code'],'label':r['label'],'vehicle_type':'truck','body_type':r['body_type'],'inner_l':r['length_mm'],'inner_w':r['width_mm'],'inner_h':h,'door_w':r.get('door_width_mm') or r['width_mm'],'door_h':r.get('door_height_mm') or h,'payload':payload,'open_top':False,'floor_load_kg_m2':r.get('floor_limit_kg_m2'),'clearance_mm':r.get('clearance_mm') or 0,'truck_parameters':r}
def axle_loads(c,pls):
    d=c.get('truck_parameters') or {};mass=sum(float(q.get('weight_kg') or 0) for q in pls)
    if not all(d.get(k) is not None for k in AXLE):return None
    moment=sum(float(q.get('weight_kg') or 0)*(float(q['x'])+float(q.get('cg_local_x') if q.get('cg_local_x') is not None else float(q['l'])/2)) for q in pls)
    span=d['support_b_x_mm']-d['support_a_x_mm'];rb=(moment-mass*d['support_a_x_mm'])/span
    ra=mass-rb
    return {'cargo_kg':mass,'cargo_cg_x_mm':moment/mass if mass else None,'cargo_a_kg':ra,'cargo_b_kg':rb,'loaded_a_kg':d['empty_a_kg']+ra,'loaded_b_kg':d['empty_b_kg']+rb,'limit_a_kg':d['limit_a_kg'],'limit_b_kg':d['limit_b_kg'],'gross_kg':mass+d['empty_a_kg']+d['empty_b_kg'],'max_gross_kg':d.get('max_gross_kg'),'basis':'二有效支承组静力模型；A可为前轴组或牵引销，B为后轴组；不等于各实际车轴载荷'}
def shift(c,pls,lo,hi,want):
    ax=axle_loads(c,pls)
    if not ax or ax['cargo_kg']<=0:return max(lo,min(want,hi))
    d=c['truck_parameters'];mass=ax['cargo_kg'];span=d['support_b_x_mm']-d['support_a_x_mm']
    low_b=max(-d['empty_b_kg'],mass+d['empty_a_kg']-d['limit_a_kg'])
    high_b=min(d['limit_b_kg']-d['empty_b_kg'],mass+d['empty_a_kg'])
    low=max(lo,(low_b-ax['cargo_b_kg'])*span/mass);high=min(hi,(high_b-ax['cargo_b_kg'])*span/mass)
    if low>high:return max(lo,min(want,hi))
    target_b=ax['gross_kg']*d['limit_b_kg']/(d['limit_a_kg']+d['limit_b_kg'])-d['empty_b_kg']
    target=(target_b-ax['cargo_b_kg'])*span/mass
    return max(low,min(target,high))
def audit(c,pls):
    if c.get('vehicle_type')!='truck':return {'hard':[],'review':[],'info':[],'axle':None}
    d=c.get('truck_parameters') or {};hard=[];review=[];info=[];mass=sum(float(q.get('weight_kg') or 0) for q in pls)
    top=max([float(q['z'])+float(q['h']) for q in pls] or [0])
    if mass>float(c['payload'])+1e-6:hard.append('货车装载质量超过填写的可装载质量')
    if top>float(c['inner_h'])+1e-6:hard.append('货物堆高超过货台以上可用高度')
    overall=None
    if d.get('deck_height_mm') is not None:
        overall=max(d.get('empty_height_mm') or 0,d['deck_height_mm']+top)
        if d.get('route_height_mm') and overall>d['route_height_mm']+1e-6:hard.append('装车总高度超过所填路线高度')
    else:review.append('缺货台离地高度，未核对装车总高度')
    if d.get('empty_height_mm') is None:review.append('缺空车整体高度，驾驶室/车厢自身高度仍需复核')
    if d.get('route_height_mm') is None:review.append('未填写本次路线允许/许可高度，不能据货台限高判定道路通过')
    tare=empty_mass(d);gross=mass+tare if tare is not None else None
    if d.get('max_gross_kg') is None:review.append('缺车辆/挂车模型范围总质量上限')
    elif mass>d['max_gross_kg']+1e-6 or (gross is not None and gross>d['max_gross_kg']+1e-6):hard.append('车辆总质量超过填写的上限（含空载质量；未知时按货物质量下界判断）')
    if tare is None:review.append('缺空载总质量，无法完整核对车辆总质量；填写独立空载质量或完整支承组空载参数')
    ax=axle_loads(c,pls)
    if not ax:review.append('未填写完整轴/支承组参数，未进行静态轴荷计算')
    else:
        for group in ('a','b'):
            value=ax['loaded_'+group+'_kg'];limit=ax['limit_'+group+'_kg']
            if value<-1e-6 or value>limit+1e-3:hard.append(f"支承组{group.upper()}载荷{value:.1f}kg不在0～{limit:g}kg内")
        info.append(ax['basis'])
    review.append('公路系固、动态倾覆、轮轴实际分配及道路/桥梁/许可须按实车专项复核')
    return {'hard':hard,'review':review,'info':info,'axle':ax,'loaded_height_mm':overall,'gross_kg':gross,'empty_mass_kg':tare}

def load_template(raw,plan):
    if not any(b.get('vehicle_type')=='truck' for b in plan.get('containers',[])):return raw
    labels={'柜型':'车型','集装箱型':'车型','柜号':'车序号','集装箱号':'车序号','装柜方案':'装车方案','Container Load List':'Truck Load List'}
    out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as z,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as dest:
        for info in z.infolist():
            data=z.read(info.filename)
            if info.filename.startswith('xl/') and info.filename.endswith('.xml'):
                root=ET.fromstring(data);changed=False
                for node in root.iter():
                    if node.tag.endswith('}t') and node.text in labels:node.text=labels[node.text];changed=True
                if changed:data=ET.tostring(root,encoding='utf-8',xml_declaration=True)
            dest.writestr(info,data)
    return out.getvalue()

class Trucks:
    def __init__(self,m):self.m=m
    @property
    def path(self):return self.m.DATA_DIR/'truck_settings.json'
    def config(self):
        if not self.path.exists():return defaults()
        try:
            d=json.loads(self.path.read_text())
            if d['mode'] not in ('truck','container'):raise ValueError('载具模式无效')
            if not isinstance(d['vehicles'],list):raise ValueError('车型列表无效')
            rows=[validate(r) for r in d['vehicles']]
            if len(rows)!=len(PRESETS) or {r['code'] for r in rows}!={p[0] for p in PRESETS}:raise ValueError('车型条目缺失或重复')
            if d['mode']=='truck' and not any(r['enabled'] for r in rows):raise ValueError('货车模式中没有启用车型')
            return {'mode':d['mode'],'vehicles':rows}
        except Exception as exc:raise ValueError('货车配置无效，请在 /vehicles 重新保存：'+str(exc))
    def mode(self):return self.config()['mode']
    def rows(self):
        rows=[as_carrier(r) for r in self.config()['vehicles'] if r['enabled']]
        if not rows:raise ValueError('请先在货车参数中启用车型并填写实际尺寸、可用高度及可装载质量')
        return rows
    def context(self):
        try:d=self.config();return {'vehicle_mode':d['mode'],'vehicle_error':'','vehicle_unit':'辆' if d['mode']=='truck' else '柜'}
        except ValueError as exc:return {'vehicle_mode':'invalid','vehicle_error':str(exc),'vehicle_unit':'台'}
    def state(self):
        rows=self.rows();return {'mode':'truck','rows':rows,'sha256':digest(rows)}
    def verified(self,c):
        row=c['truck_parameters'];eid=row.get('evidence_id');key=row['code']
        if not self.m._evidence_matches(eid,scope_type='project',scope_key=key,evidence_types=('actual_truck_parameters',),entity_types=('truck',),entity_ids=(key,),require_verified=True):return False
        try:return json.loads(self.m._evidence_summary(eid).get('claimed_values_json') or '{}').get('parameters_sha256')==digest(parameters(row))
        except Exception:return False
    def shipment_checks(self,box,data,verified):
        result=audit(box,box['placements']);out=[];name='第'+str(box['index'])+'辆货车'
        for s in result['hard']:out.append(dict(status='不满足',entity=name,page=12,message=s))
        if not self.verified(box):out.append(dict(status='待复核',entity=name,page=12,message='车型尺寸、载荷、支承组等缺当前实车参数凭证'))
        for f,label in [('dynamic_review','道路工况与抗滑/倾覆'),('lashing_review','实际车体系固点、索具及连接能力'),('line_load_review','货台、支承组及各实际车轴/牵引销载荷'),('rfs_review','车辆总质量、尺寸及道路/桥梁/超限许可')]:
            if not(verified and data.get(f)):out.append(dict(status='待复核',entity=name,page=12,message='货车专项资料未核对：'+label))
        if result['axle'] is None:out.append(dict(status='待复核',entity=name,page=12,message='缺完整静态支承组模型，不能判定轴荷通过'))
        if result['loaded_height_mm'] is None or (box.get('truck_parameters') or {}).get('empty_height_mm') is None:out.append(dict(status='待复核',entity=name,page=12,message='缺实车高度资料'))
        if (box.get('truck_parameters') or {}).get('max_gross_kg') is None:out.append(dict(status='待复核',entity=name,page=12,message='缺所建模车辆/挂车范围的总质量上限'))
        if (box.get('truck_parameters') or {}).get('route_height_mm') is None:out.append(dict(status='待复核',entity=name,page=12,message='缺本次路线高度/许可值，不能仅凭货台限高判断通过'))
        return out
    def formal_issues(self,plan,scheme):
        m=self.m;issues=[]
        if plan.get('partial'):issues.append('所选货车未全部装入货物')
        if m._draft_rows(scheme) or plan.get('invalid_manual_layouts'):issues.append('存在未保存或失效的手调布局')
        for b in plan['containers']:
            if not b['placements']:continue
            if not self.verified(b):issues.append(f"第{b['index']}辆货车未绑定当前有效实车参数凭证")
            if (b.get('engineering_audit') or {}).get('status') not in ('通过','需复核'):issues.append(f"第{b['index']}辆货车物理校核不通过/高风险")
        # A trip-specific dossier is required even outside the ASU packing profile.
        issues += [f"{r['entity']}：{r['message']}" for r in m.ASU.container_checks(plan)]
        return list(dict.fromkeys(issues))
    def install_routes(self):
        m=self.m
        @m.app.get('/vehicles')
        def page(request:Request):
            error=request.query_params.get('error','')
            try:cfg=self.config()
            except ValueError as exc:cfg=defaults();error=str(exc)
            code=request.query_params.get('code') or PRESETS[0][0];row=next((r for r in cfg['vehicles'] if r['code']==code),cfg['vehicles'][0])
            return m.templates.TemplateResponse(request,'vehicles.html',dict(config=cfg,row=row,error=error,message=request.query_params.get('message',''),release_meta=m._release_meta_load()))
        @m.app.post('/vehicle-mode')
        async def mode(request:Request):
            try:
                form=await request.form();wanted=form.get('mode')
                if wanted not in ('truck','container'):raise ValueError('载具模式无效')
                cfg=self.config();cfg['mode']=wanted
                if wanted=='truck' and not any(r['enabled'] for r in cfg['vehicles']):return RedirectResponse('/vehicles?message='+quote('先启用一种货车并填写尺寸、载荷，再开始配载。'),303)
                m._json_save(self.path,cfg);m._PERF_CACHE['fast'].clear();m._PERF_CACHE['deep'].clear()
                return RedirectResponse('/#containerSelection',303)
            except Exception as exc:return RedirectResponse('/vehicles?error='+quote(str(exc)),303)
        @m.app.post('/vehicles/reset')
        def reset():
            if self.path.exists():self.path.replace(self.path.with_name('truck_settings_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S')+'.bak'))
            m._json_save(self.path,defaults());m._PERF_CACHE['fast'].clear();m._PERF_CACHE['deep'].clear()
            return RedirectResponse('/vehicles?message='+quote('旧货车配置已备份，恢复可编辑模板并明确切回集装箱模式；业务材料未改动。'),303)
        @m.app.post('/vehicles/save')
        async def save(request:Request):
            code='';f=None;row=None;cfg=None
            try:
                f=await request.form();code=str(f.get('code') or '');cfg=self.config()
                old=next((r for r in cfg['vehicles'] if r['code']==code),None)
                if not old:raise ValueError('车型不存在')
                row=copy.deepcopy(old);row.update(code=code,label=str(f.get('label') or old['label']),enabled=f.get('enabled')=='on',body_type=f.get('body_type') or 'flatbed',reference=str(f.get('reference') or ''))
                for k in FIELDS:row[k]=f.get(k) if f.get(k) not in ('',None) else None
                row=validate(row);row['evidence_id']=old.get('evidence_id')
                upload=f.get('evidence')
                if upload and getattr(upload,'filename',None):
                    if not row['reference']:raise ValueError('登记实车凭证必须填写资料/车辆参考编号')
                    ev=m.EVIDENCE_STORE.save_evidence(entity_type='truck',entity_id=code,evidence_type='actual_truck_parameters',filename=upload.filename,content=await upload.read(),note=row['reference'],created_by=m._confirmation_user(),scope_type='project',scope_key=code,claimed_values={'parameters_sha256':digest(parameters(row))},verification={'ok':True,'method':'责任人查看实车资料后手填参数；软件未OCR验证凭证正文'})
                    row['evidence_id']=ev['id']
                cfg['vehicles']=[row if r['code']==code else r for r in cfg['vehicles']]
                if f.get('use_truck')=='on':
                    if not row['enabled']:raise ValueError('切换货车模式前请启用当前车型')
                    cfg['mode']='truck'
                if cfg['mode']=='truck' and not any(r['enabled'] for r in cfg['vehicles']):raise ValueError('停用最后一个车型前，请先明确切回集装箱模式')
                m._json_save(self.path,cfg);m._PERF_CACHE['fast'].clear();m._PERF_CACHE['deep'].clear()
                m._record_credibility_event('truck_parameters','truck',code,row['label'],{'parameters_sha256':digest(parameters(row)),'evidence_id':row.get('evidence_id')})
                return RedirectResponse('/vehicles?code='+quote(code)+'&message='+quote('车型已保存。预设不是实车额定参数，修改后旧凭证/布局会重新核对。'),303)
            except Exception as exc:
                if cfg is None:cfg=defaults()
                if not isinstance(row,dict):row=copy.deepcopy(next((r for r in cfg['vehicles'] if r['code']==code),cfg['vehicles'][0]))
                if f is not None:
                    for k in (*FIELDS,'label','reference','body_type'):
                        if k in f:row[k]=str(f[k])
                    row['enabled']=f.get('enabled')=='on'
                message=str(exc);field=next((k for k in FIELDS if k in message),None)
                return m.templates.TemplateResponse(request,'vehicles.html',dict(config=cfg,row=row,error=message,message='',field_errors={field:message} if field else {},release_meta=m._release_meta_load()),status_code=422)
