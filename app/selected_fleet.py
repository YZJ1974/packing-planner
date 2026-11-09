"""Fixed user fleet: identical physical solver, no implicit extra containers."""
import json
from urllib.parse import quote
from fastapi import Request
from fastapi.responses import RedirectResponse
from app.input_validation import number

DEFAULT={'enabled':False,'fleet':[]}
CODE='USER_SELECTED'
def load(path):
    if not path.exists():return dict(DEFAULT)
    try:
        raw=json.loads(path.read_text());enabled=raw['enabled'];fleet=raw['fleet']
        if not isinstance(enabled,bool) or not isinstance(fleet,list):raise ValueError('配置类型错误')
        result=[];seen=set()
        for r in fleet:
            code=str(r['code']);count=int(number(r['count'],'柜数',strict=True,integer=True))
            if not code or code in seen:raise ValueError('柜型为空或重复')
            seen.add(code);result.append({'code':code,'count':count})
        if sum(r['count'] for r in result)>100:raise ValueError('单次最多选择100柜')
        if enabled and not result:raise ValueError('至少选择一个集装箱')
        return {'enabled':enabled,'fleet':result}
    except Exception as exc:raise ValueError('手选集装箱配置无效，请重新保存选择或明确切换自动模式：'+str(exc))

class SelectedFleet:
    def __init__(self,m):self.m=m
    @property
    def path(self):return self.m.DATA_DIR/('selected_trucks.json' if self.m.TRUCKS.mode()=='truck' else 'selected_containers.json')
    def wording(self,text):
        if self.m.TRUCKS.mode()!='truck':return text
        for old,new in [('集装箱','货车'),('实柜','实车'),('柜型','车型'),('柜数','辆数'),('100柜','100辆'),('门洞','装卸尺寸'),('ASU边界','载具边界')]:text=text.replace(old,new)
        return text
    def config(self):
        try:return load(self.path)
        except ValueError as exc:raise ValueError(self.wording(str(exc))) from exc
    def context(self):
        error=''
        try:config=self.config()
        except ValueError as exc:config=dict(DEFAULT);error=str(exc)
        try:containers=self.m._active_containers()
        except Exception as exc:containers=[];error=str(exc)
        available={c['code'] for c in containers};missing=[r['code'] for r in config['fleet'] if r['code'] not in available]
        if config['enabled'] and missing:error='所选柜型已不在当前实柜参数中：'+', '.join(missing)+'。请重新选择，不会自动改用其他柜型。'
        return dict(fleet_config=config,fleet_containers=containers,fleet_counts={r['code']:r['count'] for r in config['fleet']},fleet_error=self.wording(error))
    def plan(self,packages,containers,items,crates,deep):
        if any(p.get('flexible_unit_volume_m3') for p in packages):raise ValueError('当前含柔性袋装体积分配，请切换自动推荐柜型和数量；固定柜数模式不将体积估算冒充逐包几何排布。')
        m=self.m;config=self.config();by_code={c['code']:c for c in containers};defs=[]
        for r in config['fleet']:
            if r['code'] not in by_code:raise ValueError('所选柜型已不在当前参数中：'+r['code']+'；请重新选择')
            for _ in range(r['count']):defs.append(dict(by_code[r['code']],_fleet_slot=len(defs)))
        if not packages:return {'packages':[],'options':[],'plans':{},'oversize':[],'selected_fleet':config,'deep_mode':bool(deep)}
        fp=m._state_fingerprint(items,crates)
        bucket=m._PERF_CACHE['deep' if deep else 'fast'];cached=m._perf_cache_get(bucket,fp)
        if cached is not None:return cached
        # First try the existing full multi-start solver. Empty selected slots are
        # restored afterwards, so the exact chosen types, quantity and slot IDs persist.
        states=m.pack_specific_bins_multistart(packages,defs);unplaced=[]
        if states is not None:
            occupied={s['container']['_fleet_slot']:s for s in states}
            states=[occupied.get(i) or m._new_bin_state(c) for i,c in enumerate(defs)]
        else:
            best=None
            # Same package-order variants as the full solver. Continue after a miss
            # to show a useful partial plan, with each omitted unit explicitly listed.
            for ordered in m._ordered_variants(packages):
                trial=[m._new_bin_state(c) for c in defs];miss=[]
                for pkg in ordered:
                    if not m._place_one(pkg,trial):miss.append(pkg)
                trial=[m._center_container_state(s) for s in trial]
                audits=[m._container_state_audit(s) for s in trial if s['placements']]
                hard,high,review=m._audit_severity_counts(audits)
                score=(len(miss),hard,high,-sum(s['weight'] for s in trial),review)
                if best is None or score<best[0]:best=(score,trial,miss)
            _,states,unplaced=best
        label=('自选货车 · ' if m.TRUCKS.mode()=='truck' else '自选集装箱 · ')+' + '.join(f"{r['code']}×{r['count']}" for r in config['fleet'])
        plan_code='TRUCK_SELECTED' if m.TRUCKS.mode()=='truck' else CODE
        plan=m._decorate_plan(states,plan_code,label);plan.update(m._plan_metrics(plan))
        # Unplaced goods are not automatically OOG or a booked extra shipment.
        plan.update(m._plan_cost(plan,crates,[],m._cost_settings_load()))
        plan['requested_fleet']=config['fleet'];plan['selected_fleet_signature']=m.asu_standard.digest(config)
        plan['unused_count']=sum(not b['placements'] for b in plan['containers']);plan['used_count']=len(states)-plan['unused_count']
        plan['unplaced_count']=len(unplaced);plan['partial']=bool(unplaced)
        plan['unplaced']=[dict(p,unplaced_reason=self.wording('所选柜型的尺寸、门洞、载荷或ASU边界不允许' if not any(m.fits_container(p,c) for c in defs) else '所选柜数不足，或当前搜索未找到满足支撑、顶载等约束的排布')) for p in unplaced]
        unit='辆' if m.TRUCKS.mode()=='truck' else '柜'
        plan['reason']=f"按所选{len(defs)}柜排布；已用{plan['used_count']}柜，空柜{plan['unused_count']}柜；已装{plan['package_count']}件，未装{len(unplaced)}件。"+('未全部装入，不能作为整票完成方案；可增加所选柜数或调整柜型后重排。' if unplaced else '当前全部运输单元已装入，仍须完成物理校核和项目资料复核。')
        if unit=='辆':plan['reason']=plan['reason'].replace('柜','车')
        if unplaced:plan['reason']+='未装货物的后续运输费用未计。'
        plan['compression_status']='柜型和数量固定，不自动增减';plan['lowest_bin_utilization']=min(b['volume_utilization'] for b in plan['containers'])
        result={'packages':packages,'options':[plan],'plans':{plan_code:plan},'oversize':plan['unplaced'],'deep_mode':bool(deep),'selected_fleet':config}
        m._perf_cache_set('deep' if deep else 'fast',fp,result);return result
    def install_routes(self):
        m=self.m
        @m.app.post('/container-selection')
        async def save(request:Request):
            try:
                f=await request.form();mode=str(f.get('mode') or '')
                if mode not in ('auto','selected'):raise ValueError('请选择自动推荐或手选柜型')
                fleet=[]
                # Auto is an explicit recovery action even if a previous selected
                # type disappeared or the actual-spec file currently needs repair.
                if mode=='selected':
                    for c in m._active_containers():
                        n=int(number(f.get('qty:'+c['code'],0),'柜数',integer=True))
                        if n:fleet.append({'code':c['code'],'count':n})
                    if not fleet:raise ValueError('至少选择一个柜型，并填写大于0的整数柜数')
                    if sum(r['count'] for r in fleet)>100:raise ValueError('单次最多选择100柜')
                config={'enabled':mode=='selected','fleet':fleet}
                m._json_save(self.path,config);m._PERF_CACHE['fast'].clear();m._PERF_CACHE['deep'].clear()
                m._record_credibility_event('select_containers','project','','手选集装箱',config)
                return RedirectResponse('/'+('?deep=1' if f.get('deep')=='1' else '')+'#containerSelection',303)
            except (ValueError,TypeError) as exc:return RedirectResponse('/?selection_error='+quote(self.wording(str(exc)))+'#containerSelection',303)
