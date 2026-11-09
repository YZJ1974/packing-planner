from __future__ import annotations
import json,datetime,math,hashlib,io,zipfile,re
from urllib.parse import quote
from fastapi import Request
from fastapi.responses import RedirectResponse,JSONResponse,Response
from app import asu_standard as rules

class AsuService:
    def __init__(self,m):self.m=m
    @property
    def path(self):return self.m.DATA_DIR/'asu_project.json'
    def profile(self):return rules.profile(self.path)
    def records(self):
        con=self.m.db()
        try:
            result={}
            for r in con.execute('SELECT * FROM asu_records'):
                try:result[r['entity_key']]=json.loads(r['record_json'])
                except (ValueError,TypeError):result[r['entity_key']]={}
            return result
        finally:con.close()
    def rebuild_pipes(self,length,max_weight,max_width,max_height):
        m=self.m
        for value,label in ((length,'管段规划长'),(max_weight,'管束最大重'),(max_width,'管束最大宽'),(max_height,'管束最大高')):m.checked_number(value,label,strict=True)
        con=m.db()
        try:
            con.execute('BEGIN IMMEDIATE')
            sources=[dict(r) for r in con.execute("SELECT * FROM pipe_sources WHERE material_family='CS' ORDER BY id")]
            if any(re.search(r'涂层|涂塑|涂漆|coated|painted',str(s.get('description') or ''),re.I) for s in sources):
                raise ValueError('源数据包含涂层管道；须按厂家托架/防护方案及实测运输包络登记，不能自动生成裸管捆。')
            old=[dict(r) for r in con.execute("SELECT * FROM items WHERE weight_source IN ('管道理论重量+ASU规划','管道理论重量+规划扎捆') AND (name LIKE 'ASU-PIPE-%' OR name LIKE 'BND-%')")]
            if any(i.get('crate_id') or i.get('size_grade')=='A' or i.get('weight_grade')=='A' or i.get('cg_confirmed') for i in old):
                raise ValueError('已有管道规划被分箱或人工确认，不能覆盖；请保留该记录并单独调整源数据。')
            for i in old:
                con.execute('DELETE FROM items WHERE id=?',(i['id'],));con.execute('DELETE FROM asu_records WHERE entity_key=?',('item:'+str(i['id']),))
            count=0;total=0
            for source in sources:
                od=m.checked_number(source['od_mm'],'管道外径',strict=True);t=m.checked_number(source['thickness_mm'],'壁厚',strict=True)
                internal=od-2*t
                if internal<=0:raise ValueError('管道内径必须大于0')
                mm=m.checked_number(source['total_length_m'],'总长度',strict=True)*1000
                n=math.ceil(mm/length);piece=mm/n;mass=m._pipe_kg_per_m(od,t,source['density_kg_m3'])*piece/1000
                small=internal<=76.2+1e-6;large=internal>355.6+1e-6
                cap=1 if small or large else max(1,min(int(max_width//od)*int(max_height//od),int(min(max_weight,5000)//max(mass,.1))))
                remaining=n
                while remaining:
                    qty=min(cap,remaining);remaining-=qty;count+=1;total+=qty
                    cols=min(max(1,int(max_width//od)),qty);rows=math.ceil(qty/cols)
                    name=f"ASU-PIPE-{source['id']}-{count}｜{source['code']}｜{'小口径待箱装' if small else '大管单件' if large else '管束'}"
                    note='按总米数均分的规划管段，非实际下料；须确认真实单根长度、内径、涂层及防护。'
                    # Small pipes are source CONTENTS; they must pass the existing full-depth
                    # wood-box solver before they become a shipping package.
                    package='木箱' if small else '裸装' if large else '裸装扎捆'
                    iid=con.execute("""INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,weight_note,size_grade,size_source,size_note,package_type,stackable,rotatable)
                    VALUES(?,?,?,?,?,?,'B','管道理论重量+ASU规划',?,'C','ASU规划管段（未实测）',?,?,0,0)""",(name,qty if small else 1,piece,od if small or large else cols*od,od if small or large else rows*od,mass if small else qty*mass,note,note,package)).lastrowid
                    data={'purpose':'equipment','goods_category':'10','packing_category':'6' if small else '1' if large else '2','pipe_kind':'small' if small else 'single' if large else 'bundle','pipe_id_mm':internal,'english_description':'Carbon steel straight pipe','notes':note}
                    con.execute('INSERT INTO asu_records VALUES(?,?,?)',('item:'+str(iid),json.dumps({'basis':'','data':data,'evidence_id':None},ensure_ascii=False),datetime.datetime.now().isoformat()))
            con.commit();return count,total,len(sources)
        except Exception:con.rollback();raise
        finally:con.close()
    def entities(self):
        con=self.m.db()
        try:
            items=[dict(r) for r in con.execute('SELECT * FROM items ORDER BY id')]
            crates=[dict(r) for r in con.execute('SELECT * FROM crates ORDER BY id')]
        finally:con.close()
        out={}
        for i in items:
            out['item:'+str(i['id'])]=dict(i,weight_kg=i['weight_kg'],type=i['package_type'],entity_label='材料 / '+i['name'],basis_data=i)
        for c in crates:
            members=[i for i in items if i.get('crate_id')==c['id']]
            if not members:continue
            weight=self.m._crate_weight_result(c,members)['gross_weight']
            out['crate:'+str(c['id'])]=dict(c,weight_kg=weight,type='木箱',entity_label='运输木箱 / '+c['name'],basis_data={'crate':c,'items':members})
        return out
    def basis(self,key,entity=None):
        if key=='project':
            paths=[self.m._active_packing_template_path(),self.m._active_shipping_mark_template_path()]
            return rules.digest({'profile':self.profile(),'templates':[hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]})
        entity=entity or self.entities().get(key)
        if not entity:raise ValueError('对象已不存在，请刷新')
        return rules.digest({'profile':self.profile(),'data':entity['basis_data']})
    def verified(self,key,record,basis):
        try:
            if not record or record.get('basis')!=basis:return False
            eid=record.get('evidence_id')
            if not self.m._evidence_matches(eid,scope_type='project',scope_key=key,evidence_types=('asu_project_review',),entity_types=('asu_record',),entity_ids=(key,),require_verified=True):return False
            ev=self.m._evidence_summary(eid);claim=json.loads(ev.get('claimed_values_json') or '{}')
            return claim.get('basis')==basis and claim.get('data_hash')==rules.digest(record.get('data') or {})
        except Exception:return False
    def group(self,item,records=None):
        if not self.profile()['enabled']:return ''
        records=self.records() if records is None else records
        key='item:'+str(item.get('id') or item.get('source_id') or item.get('name'))
        d=records.get(key,{}).get('data') or {}
        if not d.get('purpose'):return 'unclassified:'+key
        # Restricted and dangerous items never mix, even with the same high-level label.
        suffix=key if d['purpose'] in ('restricted','dangerous') else ''
        return d['purpose']+':'+suffix
    def augment(self,packages):
        if not self.profile()['enabled']:return packages
        records=self.records();entities=self.entities()
        for pkg in packages:
            key=pkg.get('asu_key');record=records.get(key,{})
            entity=entities.get(key)
            valid=bool(entity and self.verified(key,record,self.basis(key,entity)))
            d=record.get('data') or {}
            pkg['asu_data']=d;pkg['asu_verified']=valid
            pkg['asu_cg_mark_required']=float(pkg['weight_kg'])>=500 or float(pkg['height_mm'])>=1000
            if valid and d.get('not_stackable_approved'):pkg['stackable']=False;pkg['max_top_load_kg']=None
            if valid and d.get('top_capacity_kg') is not None and pkg.get('max_top_load_kg') is not None:
                pkg['max_top_load_kg']=min(pkg['max_top_load_kg'],d['top_capacity_kg'])
            pkg['lashing_required']=True  # All ASU shipments need a securing plan.
        return packages
    def project_checks(self,records=None):
        p=self.profile()
        if not p['enabled']:return []
        records=self.records() if records is None else records;r=records.get('project',{});d=r.get('data') or {}
        valid=self.verified('project',r,self.basis('project'));out=[]
        for flag,msg,page in [('source_confirmed','正式批准版本及运输场景未确认',1),('conflicts_resolved','中英文、边界及侧挡架歧义未有项目书面澄清',52),('rfs_review','RFS资料、检验放行及交货期限未核对',43),('template_approved','缺本项目MPL/DPL与唛头正式模板批准',45),('platform_receipt','缺Supplier Connect提交回执或平台不可用时的认可替代记录',44)]:
            if not (valid and d.get(flag)):out.append(dict(status='待复核',entity='项目',page=page,message=msg))
        if p['bottom_basis']=='unconfirmed':out.append(dict(status='待复核',entity='项目',page=10,message='底部100mm的计量基准未书面确认；软件不对已有包装重复抬高'))
        if not p['delivery_date']:out.append(dict(status='待复核',entity='项目',page=43,message='缺计划交货日期，无法核对资料期限'))
        return out
    def package_report(self):
        items,crates=self.m.fetch_state();packages=self.m.get_packages(items,crates)
        out=self.project_checks()
        if not self.profile()['enabled']:return out,packages
        for item in items:
            if item.get('package_type')=='木箱' and not item.get('crate_id') and not item.get('is_package_placeholder'):
                out.append(dict(status='不满足',entity=item['name'],page=12,message='材料尚未分配到实际木箱，不能从整票运输单元中遗漏'))
        for pkg in packages:
            out+=rules.package_checks(pkg,pkg.get('asu_data') or {},pkg.get('asu_verified',False),self.profile())
            if not any(self.m.fits_container(pkg,c) for c in self.m._active_containers()):
                out.append(dict(status='不满足',entity=pkg['name'],page=12,message='未能装入扣除ASU间隙后的已配置载具；需要其他经批准的运输方案，不能在本装柜方案中遗漏'))
            # Current geometry, not the saved old PASS label, is authoritative for wood boxes.
            if pkg.get('type')=='木箱':
                cid=int(pkg['asu_key'].split(':')[1]);c=next(c for c in crates if c['id']==cid)
                if not self.m._is_prepacked(c):
                    records=self.records();groups={self.group(i,records) for i in items if i.get('crate_id')==cid}
                    known={g for g in groups if not g.startswith('unclassified:')}
                    if len(known)>1:
                        out.append(dict(status='不满足',entity=c['name'],page=28,message='实际箱内含不同用途类别，设备/散件/备件/工具等应按规定隔离；不能绕过分箱约束'))
                    if any(g.startswith('unclassified:') for g in groups):
                        out.append(dict(status='待复核',entity=c['name'],page=15,message='箱内材料用途分类不完整，需按实际内容逐项确认'))
                    con=self.m.db()
                    try:pls=[dict(q) for q in con.execute('SELECT * FROM crate_placements WHERE crate_id=?',(cid,))]
                    finally:con.close()
                    geom=self.m._crate_geometry(c['length_mm'],c['width_mm'],c['height_mm'],sum(i['qty']*i['weight_kg'] for i in items if i.get('crate_id')==cid),c.get('layout_padding_mm') if c.get('layout_padding_mm') is not None else 100)
                    if not pls or any(float(q[a])+float(q[b])>geom[g]+1e-6 for q in pls for a,b,g in [('x','l','usable_l'),('y','w','usable_w'),('z','h','usable_h')]):
                        out.append(dict(status='不满足',entity=c['name'],page=31,message='现有箱内布局未按当前ASU结构净空复核通过；请重新分箱/调整并复核'))
        return out,packages
    def container_context(self,scheme,index):
        items,crates=self.m.fetch_state();rec=self.m._apply_saved_container_layouts(self.m.recommend_containers(items,crates))
        plan=(rec.get('plans') or {}).get(scheme)
        if not plan:raise ValueError('当前柜方案不存在')
        box=next((b for b in plan['containers'] if b['index']==index),None)
        if not box:raise ValueError('柜号不存在')
        basis=rules.digest({'profile':self.profile(),'box':box,'container_specs':self.m._planning_specs_state()})
        return plan,box,basis
    def container_checks(self,plan):
        if not self.profile()['enabled'] and not any(b.get('vehicle_type')=='truck' for b in plan.get('containers',[])):return []
        out=[];records=self.records()
        from collections import Counter
        items,crates=self.m.fetch_state();expected=Counter((p.get('asu_key'),p['name']) for p in self.m.get_packages(items,crates))
        actual=Counter((p.get('asu_key'),p['name']) for box in plan.get('containers') or [] for p in box['placements'])
        if expected!=actual:out.append(dict(status='不满足',entity='整票',page=12,message='装柜方案未覆盖全部运输单元，不能以部分装入结果放行整票'))
        for box in plan.get('containers') or []:
            if not box.get('placements'):continue  # Unused selected slot is not a packed shipment.
            key=f"container:{plan['code']}:{box['index']}";r=records.get(key,{});d=r.get('data') or {}
            # Same normalized basis as the editor, independent of whether saved layouts were overlaid twice.
            basis=rules.digest({'profile':self.profile(),'box':box,'container_specs':self.m._planning_specs_state()})
            valid=self.verified(key,r,basis);name=f"第{box['index']}柜"
            if box.get('vehicle_type')=='truck':
                out+=self.m.TRUCKS.shipment_checks(box,d,valid)
                continue
            def issue(msg,page=50,status='待复核'):out.append(dict(status=status,entity=name,page=page,message=msg))
            for msg in rules.clearance_issues(self.profile(),box):issue(msg,10,'不满足')
            if not valid:issue('装柜系固/地板与墙载荷/VGM资料未绑定当前布局',45)
            for f,msg in [('line_load_review','缺实际地板纵向分布与支承反力复核'),('wall_load_review','缺侧墙/端墙及挡块传力复核'),('dynamic_review','缺实际运输工况、摩擦、防滑/倾覆复核'),('lashing_review','缺系固MSL、角度、连接和传力能力专项复核')]:
                if not (valid and d.get(f)):issue(msg)
            total=sum(float(q['weight_kg']) for q in box['placements'])
            carried=self.m.support_load_analysis(box['placements'])['carried']
            line=rules.longitudinal_demand([dict(q,weight_kg=float(q['weight_kg'])+carried.get(id(q),0)) for q in box['placements']])
            if d.get('line_limit_kg_m') is None:issue('缺实柜纵向允许负荷，40尺2.5–3.0t/m不能直接取上限')
            elif line>d['line_limit_kg_m']+1e-6:issue(f'均布筛查纵向需求{line:.1f}kg/m超过登记限值；仍需实际支承反力分析',50,'不满足')
            payload=float(box['payload']);g=9.80665
            for k,ratio in [('wall_side_kN',.6),('wall_end_kN',.4)]:
                if d.get(k) is None:issue('缺'+k+'，不得以均布墙载荷比例代替局部点载荷能力')
                elif d[k]>payload*ratio*g/1000+1e-6:issue(k+'超过文件所述均布能力比例',50,'不满足')
            for q in box['placements']:
                pressure=self.m.floor_contact_pressure_kg_m2(q,carried.get(id(q),0))
                if pressure is not None and pressure>=10000-1e-6:
                    if not (valid and d.get('structural_review')):issue(str(q['name'])+'含上层保守传载的接触压力达到10t/m²，须事先批准',32)
            if not d.get('container_no'):issue('缺实际箱号',45)
            if not (valid and d.get('vgm_certificate')):issue('缺VGM核实方法、重量证明和签署核对',45)
            values=[d.get(k) for k in ('container_tare_kg','dunnage_kg','securing_kg','vgm_kg')]
            if any(v is None for v in values):issue('VGM必须登记柜皮重、额外垫料、系固料及核实总质量；已有包装不得重复计重',45)
            else:
                tare,dunnage,securing,vgm=values;expected=total+tare+dunnage+securing
                if tare<=0 or vgm<expected-0.1:issue('VGM低于当前货物+柜皮+另加垫料/系固材料质量，或柜皮无效',45,'不满足')
                if d.get('vgm_method')=='sum' and abs(vgm-expected)>.1:issue('分项求和核实质量与当前分项合计不一致（0.1kg仅为数值比对精度）',45,'不满足')
                if vgm>payload+tare+1e-6:issue('核实总质量超过当前载荷上限+柜皮重',45,'不满足')
            if d.get('vgm_method') not in ('whole_weigh','sum'):issue('请选择整柜称重或合规分项求和，并提供相应凭证',45)
        return out
    def gate(self,plan=None):
        issues,_=self.package_report()
        if plan:issues+=self.container_checks(plan)
        return list(dict.fromkeys(f"ASU {r['entity']}：{r['message']}（第{r['page']}页）" for r in issues))
    def install_routes(self):
        m=self.m
        @m.app.get('/asu-project')
        def page(request:Request):
            error=request.query_params.get('error','')
            try:p=self.profile()
            except ValueError as exc:p=rules.DEFAULT.copy();error=str(exc)
            records=self.records();entities=self.entities();key=request.query_params.get('entity') or 'project'
            entity=entities.get(key);plan=None;box=None
            if key.startswith('container:'):
                try:
                    _,scheme,index=key.split(':');plan,box,basis=self.container_context(scheme,int(index));entity={'name':key,'weight_kg':box['total_weight'],'entity_label':key,'basis_data':box}
                except ValueError as exc:error=str(exc);key='project'
            if key=='project':basis=self.basis('project') if not error else rules.digest(p)
            elif not key.startswith('container:'):
                if not entity:key='project';basis=self.basis('project')
                else:basis=self.basis(key,entity)
            try:issues,packages=self.package_report() if p['enabled'] else ([],[])
            except ValueError as exc:issues=[dict(status='待复核',entity='数据',page=45,message=str(exc))];packages=[]
            items,crates=m.fetch_state();rec=m._apply_saved_container_layouts(m.recommend_containers(items,crates)) if not error else {'options':[],'plans':{}}
            container_links=[(f"container:{code}:{b['index']}",f"{code} 第{b['index']}柜") for code,pl in (rec.get('plans') or {}).items() for b in pl['containers']]
            if plan:issues+=self.container_checks(plan)
            record=records.get(key,{});data=record.get('data') or {}
            return m.templates.TemplateResponse(request,'asu_project.html',dict(profile=p,standard=rules.STANDARD,source_sha256=rules.SOURCE_SHA256,footnote=rules.FOOTNOTE,entities=entities,key=key,entity=entity,container_links=container_links,basis=basis,data=data,valid=self.verified(key,record,basis),issues=issues,error=error,message=request.query_params.get('message',''),purposes=rules.PURPOSES,accel=rules.ACCEL,forces=rules.forces(entity['weight_kg'],p) if entity else [],checklist=rules.checklist(p['delivery_date']),minimum_panel=rules.panel_min(p),truck_box=bool(box and box.get('vehicle_type')=='truck'),release_meta=m._release_meta_load(),pipe_req=rules.pipe_requirements(entity['weight_kg'],data['pipe_kind']) if entity and data.get('pipe_kind') in ('bundle','single','coated') else None))
        @m.app.post('/asu/settings')
        async def settings(request:Request):
            try:
                f=await request.form();p=rules.validate_profile(dict(rules.DEFAULT,enabled=f.get('enabled')=='on',transport_modes=f.getlist('transport_modes'),panel_material=f.get('panel_material'),delivery_date=str(f.get('delivery_date') or ''),project_reference=str(f.get('project_reference') or ''),bottom_basis=f.get('bottom_basis') or 'unconfirmed'))
                m._json_save(self.path,p);m._PERF_CACHE['fast'].clear();m._PERF_CACHE['deep'].clear()
                with m._REPACK_CACHE_LOCK:m._REPACK_CACHE.clear();m._REPACK_CACHE_ORDER.clear()
                m._record_credibility_event('asu_settings','project','','ASU模式',p)
                return RedirectResponse('/asu-project?message='+quote('项目模式已保存；相关旧布局和确认将重新核对。'),303)
            except Exception as exc:return RedirectResponse('/asu-project?error='+quote(str(exc)),303)
        @m.app.post('/asu/record')
        async def save_record(request:Request):
            key='project'
            try:
                f=await request.form();key=str(f.get('entity_key') or 'project');entity=None
                if key.startswith('container:'):
                    _,scheme,index=key.split(':');_,box,basis=self.container_context(scheme,int(index))
                else:
                    entity=self.entities().get(key);basis=self.basis(key,entity)
                if str(f.get('basis') or '')!=basis:raise ValueError('页面数据已变化，请刷新后重新核对')
                d=rules.parse_record(f,[entity[k] for k in ('length_mm','width_mm','height_mm')] if entity else None)
                user=m._confirmation_user();upload=f.get('evidence')
                if not upload or not getattr(upload,'filename',None) or not d.get('approval_ref'):raise ValueError('须填写批准/资料编号并上传本次核对依据；勾选项仅代表责任人声明')
                raw=await upload.read()
                ev=m.EVIDENCE_STORE.save_evidence(entity_type='asu_record',entity_id=key,evidence_type='asu_project_review',filename=upload.filename,content=raw,note=d['approval_ref'],created_by=user,scope_type='project',scope_key=key,claimed_values={'basis':basis,'data_hash':rules.digest(d)},verification={'ok':True,'method':'责任人依据附件逐项声明；软件未OCR、未验证附件中的工程结论'})
                if key.startswith('container:'):_,_,current=self.container_context(scheme,int(index))
                else:current=self.basis(key)
                if current!=basis:raise ValueError('核对期间对象已变化，本次资料未生效，请刷新')
                record={'basis':basis,'data':d,'evidence_id':ev['id'],'reviewer':user}
                con=m.db()
                try:con.execute('INSERT OR REPLACE INTO asu_records(entity_key,record_json,updated_at) VALUES(?,?,?)',(key,json.dumps(record,ensure_ascii=False),datetime.datetime.now().isoformat()));con.commit()
                finally:con.close()
                m._record_credibility_event('asu_record','asu_record',key,d['approval_ref'],{'evidence_id':ev['id'],'basis':basis});m._PERF_CACHE['fast'].clear();m._PERF_CACHE['deep'].clear()
                return RedirectResponse('/asu-project?entity='+quote(key)+'&message='+quote('资料已登记。数值不足、未关闭条款和过期数据仍会阻止数据对照版。'),303)
            except Exception as exc:return RedirectResponse('/asu-project?entity='+quote(key)+'&error='+quote(str(exc)),303)
        @m.app.get('/asu/report.json')
        def report():
            issues,packages=self.package_report()
            status='未启用ASU约束' if not self.profile()['enabled'] else '待项目复核' if issues else '已登记输入；仍需项目签字'
            return JSONResponse({'standard':rules.STANDARD,'profile':self.profile(),'status':status,'issues':issues,'force_demands':{p['name']:rules.forces(p['weight_kg'],self.profile()) for p in packages},'scope':'资料完整性与已实现数值校核，不是结构/绑扎/运输签证'})
        @m.app.get('/asu/transport.svg')
        def transport(entity:str):
            from app.asu_drawing import drawing
            current=self.entities().get(entity)
            if not current:raise ValueError('对象不存在')
            record=self.records().get(entity,{})
            items,crates=m.fetch_state();pkg=next((q for q in m.get_packages(items,crates) if q.get('asu_key')==entity),current)
            raw=drawing(pkg,record.get('data') or {},self.verified(entity,record,self.basis(entity,current)))
            return Response(raw,media_type='image/svg+xml',headers={'Content-Disposition':'attachment; filename="ASU_Transport_Geometry_DRAFT.svg"'})
        @m.app.get('/asu/packing-files')
        def packing_files(mode:str='planning'):
            if mode not in ('planning','formal'):raise ValueError('导出模式无效')
            if not self.profile()['enabled']:raise ValueError('请先启用ASU项目模式')
            items,crates=m.fetch_state()
            if mode=='formal':
                issues=m._data_quality_release_issues(items,crates,asu_split=True)
                if issues:return JSONResponse({'ok':False,'error':'ASU分单门禁未通过','issues':issues},status_code=409)
            rows=m._packing_list_records(items,crates);groups={}
            for r in rows:groups.setdefault((r.get('po') or 'PO_PENDING',r.get('batch') or 'BATCH_PENDING'),[]).append(r)
            if not groups:raise ValueError('没有箱单记录')
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
                for n,((po,batch),values) in enumerate(sorted(groups.items()),1):
                    for r in values:r['remark']=('DRAFT / NOT FOR RELEASE; ' if mode=='planning' else 'DATA CROSS-CHECK ONLY; PROJECT SIGN-OFF REQUIRED; ')+str(r.get('remark') or '')
                    raw=m._xlsx_fill_template(m._active_packing_template_path().read_bytes(),values,m.PACKING_EXPORT_ALIASES)
                    z.writestr(f'{n:03d}_Packing_List_{"DRAFT" if mode=="planning" else "DATA_CROSSCHECK"}.xlsx',raw)
                z.writestr('order_batch_index.json',json.dumps([{'file_index':n,'po':po,'batch':batch,'row_count':len(vals)} for n,((po,batch),vals) in enumerate(sorted(groups.items()),1)],ensure_ascii=False,indent=2))
            return Response(buf.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="ASU_Packing_Lists.zip"'})
