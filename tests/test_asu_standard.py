import unittest,json,math,io,zipfile
from unittest.mock import patch
from xml.etree import ElementTree as ET
from test_v58_regression import IsolatedDbMixin,m,VALID_PDF
from fastapi.testclient import TestClient
from app import asu_standard as a

class ASU(IsolatedDbMixin,unittest.TestCase):
    def setUp(self):
        super().setUp();self.set_user();self.path=m.DATA_DIR/'asu_project.json'
        m._json_save(self.path,dict(a.DEFAULT,enabled=True));self.client=TestClient(m.app,raise_server_exceptions=True)
    def tearDown(self):
        self.path.unlink(missing_ok=True);super().tearDown()
    def seed(self,name='Frame unit',package='裸装',qty=1,weight=100):
        con=m.db();iid=con.execute('INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,package_type,stackable,rotatable) VALUES(?,?,800,600,400,?,?,0,0)',(name,qty,weight,package)).lastrowid;con.commit();con.close();m.fetch_state();return iid
    def record(self,key,**extra):
        d=dict(english_description='Instrument frame',purpose='equipment',goods_category='1',packing_category='1',po='PO1',batch='B1',origin='China',net_kg=100,storage_category='1',storage_months=12,approval_ref='APP-01',measurements_actual='on',dynamic_review='on',lashing_review='on',marking_review='on',preservation_review='on')
        d.update(extra);d.update(entity_key=key,basis=m.ASU.basis(key))
        r=self.client.post('/asu/record',data=d,files={'evidence':('review.pdf',VALID_PDF,'application/pdf')},follow_redirects=False)
        self.assertEqual(r.status_code,303,r.text);self.assertNotIn('error=',r.headers['location']);return m.ASU.records()[key]
    def plan(self):
        r=m.recommend_containers(*m.fetch_state());return r['plans'][r['options'][0]['code']]
    def test_default_generic_without_profile(self):
        self.path.unlink();self.assertFalse(m._asu_profile()['enabled']);self.assertEqual(m._crate_structure_profile(100,1000)[1]['panel_t_mm'],5)
    def test_panel_minimum_applies_and_net_space_and_tare_change(self):
        p=m._crate_structure_profile(100,1000)[1];self.assertEqual(p['panel_t_mm'],12.5);self.assertEqual(p['runner_h_mm'],100)
        asu=m._crate_geometry(1000,1000,1000,100,50);tare=m._crate_structure_tare(1000,1000,1000,100)['total_kg']
        self.path.unlink();generic=m._crate_geometry(1000,1000,1000,100,50);other=m._crate_structure_tare(1000,1000,1000,100)['total_kg']
        self.assertLess(asu['usable_l'],generic['usable_l']);self.assertLess(asu['usable_h'],generic['usable_h']);self.assertGreater(tare,other)
    def test_asu_cannot_fall_back_to_legacy_tare(self):
        expected=m._estimate_tare(1000,1000,1000,1,100)
        old=m._CRATE_WEIGHT_SETTINGS_CACHE;m._CRATE_WEIGHT_SETTINGS_CACHE=dict(m._crate_weight_settings_load(),model='legacy')
        try:self.assertEqual(expected,m._estimate_tare(1000,1000,1000,1,100))
        finally:m._CRATE_WEIGHT_SETTINGS_CACHE=old
    def test_air_and_solid_wood_minimum(self):
        p=dict(a.DEFAULT,enabled=True,transport_modes=['air']);self.assertEqual(a.panel_min(p),9.5)
        self.assertEqual(a.panel_min(dict(p,panel_material='solid_wood')),24)
        self.assertEqual(a.panel_min(dict(p,transport_modes=['air','sea'])),12.5)
        self.assertEqual(a.panel_min(dict(p,transport_modes=['road'])),12.5)
    def test_runner_table_boundaries(self):
        self.assertEqual([a.runners(v) for v in [1000,1000.1,1800,1800.1,2750,2750.1,3250,3250.1]],[2,3,3,4,4,5,5,None])
    def test_flush_package_rejected_by_fit_and_manual_audit(self):
        c=m.DEFAULT_CONTAINERS[2];p=dict(name='wide',length_mm=c['inner_l'],width_mm=600,height_mm=400,weight_kg=100,rotatable=False)
        self.assertFalse(m.fits_container(p,c));q=dict(p,x=0,y=100,z=0,l=800,w=600,h=400)
        box=dict(container_code=c['code'],inner_l=c['inner_l'],inner_w=c['inner_w'],inner_h=c['inner_h'],payload=c['payload'],placements=[q])
        self.assertTrue(any('100mm' in x for x in m._container_physical_issues(box)))
    def test_automatic_and_centered_placements_keep_clearance(self):
        self.seed(qty=4);plan=self.plan()
        for b in plan['containers']:
            self.assertFalse(a.clearance_issues(m._asu_profile(),b))
            self.assertTrue(all(p['x']>=100 and p['y']>=100 for p in b['placements']))
    def test_container_gap_not_applied_inside_wood_boxes(self):
        c=dict(code='WOODBOX',inner_l=1000,inner_w=1000,inner_h=1000,payload=1000,door_w=1000,door_h=1000)
        self.assertEqual(m._new_bin_state(c)['points'][0],(0,0,0))
    def test_wood_sources_without_classification_are_isolated(self):
        self.seed('A','木箱');self.seed('B','木箱');m.apply_smart_crates(1800,1500,1500,4000,50,45)
        items,crates=m.fetch_state();self.assertEqual(len(crates),2);self.assertEqual(sum(i['qty'] for i in items),2)
    def test_declared_equipment_and_spares_cannot_mix(self):
        x=self.seed('A','木箱');y=self.seed('B','木箱');self.record('item:'+str(x));self.record('item:'+str(y),purpose='two_year_spare',storage_months=24)
        m.apply_smart_crates(1800,1500,1500,4000,50,45);self.assertEqual(len(m.fetch_state()[1]),2)
    def test_direct_assignment_cannot_bypass_purpose_separation(self):
        x=self.seed('A','木箱');y=self.seed('B','木箱');self.record('item:'+str(x));self.record('item:'+str(y),purpose='two_year_spare',storage_months=24)
        m.apply_smart_crates(1800,1500,1500,4000,50,45)
        con=m.db();cid=con.execute('SELECT crate_id FROM items WHERE id=?',(x,)).fetchone()[0];con.execute('UPDATE items SET crate_id=? WHERE id=?',(cid,y));con.commit();con.close()
        self.assertTrue(any(r['status']=='不满足' and '不同用途' in r['message'] for r in m.ASU.package_report()[0]))
    def test_small_pipe_routes_to_contents_not_prepacked_or_bare_bundle(self):
        con=m.db();con.execute("INSERT INTO pipe_sources(code,category,material,total_length_m,od_mm,thickness_mm,density_kg_m3,material_family) VALUES('DN50','PIPE','CS',12,60.3,3.91,7850,'CS')");con.commit();con.close()
        result=m.rebuild_pipe_bundles();items,crates=m.fetch_state();self.assertEqual(result[1],2)
        self.assertTrue(items and all(i['package_type']=='木箱' and not i['is_package_placeholder'] for i in items));self.assertFalse(crates)
        m.apply_smart_crates(7000,1500,1500,5000,100,45);self.assertTrue(m.fetch_state()[1])
    def test_known_coated_source_cannot_be_generated_as_bare_bundle(self):
        con=m.db();con.execute("INSERT INTO pipe_sources(code,category,description,material,total_length_m,od_mm,thickness_mm,density_kg_m3,material_family) VALUES('DN100','PIPE','coated pipe','CS',12,114.3,6,7850,'CS')");con.commit();con.close()
        with self.assertRaisesRegex(ValueError,'托架'):m.rebuild_pipe_bundles()
        self.assertFalse(m.fetch_state()[0])
    def test_unboxed_wood_contents_cannot_disappear_from_release_gate(self):
        self.seed(package='木箱');self.assertTrue(any('尚未分配' in r['message'] for r in m.ASU.package_report()[0]))
    def test_pipe_formulas_and_coated_not_given_invented_counts(self):
        r=a.pipe_requirements(13000,'bundle');self.assertEqual((r['beddings'],r['belts'],r['belts_per_side'],r['MSL_kN']),(7,13,7,25))
        r=a.pipe_requirements(11000,'single');self.assertEqual((r['beddings'],r['belts']),(6,8));self.assertIsNone(a.pipe_requirements(11000,'coated')['belts'])
    def test_forces_preserve_modes_and_footnotes(self):
        f=a.forces(1000,dict(a.DEFAULT,transport_modes=['road','sea']));self.assertEqual(f[0]['inertia_kN'][0],round(1000*9.80665*.8/1000,4));self.assertEqual(f[1]['g'],[.4,.4,.8,1]);self.assertIn('不含静重',a.FOOTNOTE)
    def test_unknown_securement_is_always_reviewed(self):
        self.seed();p=m.get_packages(*m.fetch_state())[0];self.assertTrue(p['lashing_required']);issues=m.ASU.gate();self.assertTrue(any('系固' in x for x in issues))
    def test_record_binding_survives_read_but_not_mass_change(self):
        iid=self.seed();key='item:'+str(iid);r=self.record(key);self.assertTrue(m.ASU.verified(key,r,m.ASU.basis(key)))
        self.client.get('/');self.assertTrue(m.ASU.verified(key,r,m.ASU.basis(key)))
        con=m.db();con.execute('UPDATE items SET weight_kg=101 WHERE id=?',(iid,));con.commit();con.close();self.assertFalse(m.ASU.verified(key,r,m.ASU.basis(key)))
    def test_record_tamper_or_wrong_object_not_approved(self):
        iid=self.seed();key='item:'+str(iid);r=self.record(key);r['data']['net_kg']=1
        self.assertFalse(m.ASU.verified(key,r,m.ASU.basis(key)))
        other=self.seed('Other');self.assertFalse(m.ASU.verified('item:'+str(other),r,m.ASU.basis('item:'+str(other))))
    def test_stale_form_submit_cannot_confirm(self):
        iid=self.seed();key='item:'+str(iid);basis=m.ASU.basis(key)
        con=m.db();con.execute('UPDATE items SET height_mm=401 WHERE id=?',(iid,));con.commit();con.close()
        r=self.client.post('/asu/record',data={'entity_key':key,'basis':basis,'approval_ref':'OLD'},files={'evidence':('x.pdf',VALID_PDF,'application/pdf')},follow_redirects=False)
        self.assertIn('error=',r.headers['location']);self.assertNotIn(key,m.ASU.records())
    def test_numeric_nonfinite_negative_and_bad_points_rejected(self):
        for value in ('nan','inf','-1'):
            with self.assertRaises(ValueError):a.parse_record({'net_kg':value})
        with self.assertRaises(ValueError):a.points('[{"x":1001,"y":0,"z":0,"capacity_kg":1}]',(1000,800,600))
    def test_insufficient_numeric_capacity_not_overridden_by_all_flags(self):
        pkg=dict(name='Wood',type='木箱',length_mm=1000,width_mm=1100,height_mm=1000,weight_kg=100,cg_confirmed=True,stackable=False)
        d=dict(net_kg=100,panel_material='plywood',panel_mm=12,ply_layers=5,top_capacity_kg=1000,runner_count=2,bottom_clearance_mm=100)
        d.update({k:True for k in a.FLAGS});d['not_stackable_approved']=False
        out=a.package_checks(pkg,d,True,m._asu_profile());self.assertTrue(any(r['status']=='不满足' and '板厚' in r['message'] for r in out));self.assertTrue(any('1100' in r['message'] for r in out))
    def test_light_tall_package_needs_cg_mark(self):
        pkg=dict(name='tall',type='裸装',length_mm=500,width_mm=500,height_mm=1000,weight_kg=20,cg_confirmed=False)
        self.assertTrue(any('高度≥1m' in r['message'] for r in a.package_checks(pkg,{},False,m._asu_profile())))
    def test_project_template_change_invalidates_approval(self):
        r=self.record('project');before=m.ASU.basis('project');self.assertTrue(m.ASU.verified('project',r,before))
        with patch.object(m,'_active_packing_template_path',return_value=self.path):self.assertNotEqual(m.ASU.basis('project'),before)
    def test_profile_change_invalidates_cached_plan_and_record(self):
        iid=self.seed();r=self.record('item:'+str(iid));first=m._state_fingerprint(*m.fetch_state())
        m._json_save(self.path,dict(a.DEFAULT,enabled=True,transport_modes=['air']))
        self.assertNotEqual(first,m._state_fingerprint(*m.fetch_state()));self.assertFalse(m.ASU.verified('item:'+str(iid),r,m.ASU.basis('item:'+str(iid))))
    def test_pages_escape_input_and_render_record_editor(self):
        iid=self.seed('<script>bad</script>');r=self.client.get('/asu-project?entity=item:'+str(iid));self.assertEqual(r.status_code,200,r.text[:200]);self.assertNotIn('<script>bad</script>',r.text);self.assertIn('真实吊点 JSON',r.text)
        self.assertEqual(self.client.get('/asu-project').status_code,200)
    def test_formal_exports_blocked_while_planning_svg_allowed(self):
        self.seed();p=self.plan();b=p['containers'][0]
        r=self.client.post('/export-container-svg',json={'scheme':p['code'],'container_index':b['index'],'mode':'formal'});self.assertEqual(r.status_code,409);self.assertIn('ASU',r.text)
        r=self.client.post('/export-container-svg',json={'scheme':p['code'],'container_index':b['index'],'mode':'planning'});self.assertEqual(r.status_code,200);self.assertIn('DRAFT',r.text)
        self.assertEqual(self.client.get('/asu/packing-files?mode=formal').status_code,409)
    def test_real_points_geometry_export_is_draft_and_escaped(self):
        iid=self.seed();key='item:'+str(iid);self.record(key,lifting_points_json='[{"label":"L1","x":10,"y":10,"z":400,"capacity_kg":200},{"label":"L2","x":790,"y":590,"z":400,"capacity_kg":200}]',sling_angle_deg=60)
        r=self.client.get('/asu/transport.svg?entity='+key);self.assertEqual(r.status_code,200);ET.fromstring(r.content);self.assertIn('L1',r.text);self.assertIn('DRAFT',r.text);self.assertIn('cm',r.text)
    def test_english_net_weight_and_order_batch_export_separation(self):
        x=self.seed('设备甲');y=self.seed('设备乙');self.record('item:'+str(x),net_kg=90);self.record('item:'+str(y),po='PO2',batch='B2',net_kg=80)
        rows=m._packing_list_records(*m.fetch_state());self.assertEqual({r['content'] for r in rows},{'Instrument frame'});self.assertEqual({r['net_weight'] for r in rows},{80,90})
        r=self.client.get('/asu/packing-files');self.assertEqual(r.status_code,200)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:self.assertEqual(len([n for n in z.namelist() if n.endswith('.xlsx')]),2);self.assertEqual(len(json.loads(z.read('order_batch_index.json'))),2)
    def test_container_editor_and_missing_vgm_gate(self):
        self.seed();p=self.plan();key=f"container:{p['code']}:1";r=self.client.get('/asu-project?entity='+key);self.assertEqual(r.status_code,200,r.text[:300]);issues=m.ASU.container_checks(p);self.assertTrue(any('VGM' in x['message'] for x in issues))
    def test_malformed_profile_has_repair_page_not_silent_generic_fallback(self):
        self.path.write_text('{broken');self.assertEqual(self.client.get('/').status_code,409);self.assertEqual(self.client.get('/asu-project').status_code,200)
    def test_saved_layout_signature_changes_with_profile(self):
        self.seed();b=self.plan()['containers'][0];before=m._container_box_signature(b);m._json_save(self.path,dict(a.DEFAULT,enabled=False));self.assertNotEqual(before,m._container_box_signature(b))
    def test_clearance_exact_boundary_permitted(self):
        c=m.DEFAULT_CONTAINERS[2];pkg=dict(name='full clear',length_mm=c['inner_l']-200,width_mm=c['inner_w']-200,height_mm=500,weight_kg=100,rotatable=False,stackable=False)
        self.assertTrue(m.fits_container(pkg,c));cand=m._can_place(pkg,(pkg['length_mm'],pkg['width_mm'],500),(100,100,0),m._new_bin_state(c),c);self.assertIsNotNone(cand)
    def test_complete_asu_records_can_close_implemented_checks(self):
        m._json_save(self.path,dict(a.DEFAULT,enabled=True,delivery_date='2099-01-01',bottom_basis='equipment_underface_includes_supports'))
        iid=self.seed();self.record('item:'+str(iid));self.record('project',source_confirmed='on',conflicts_resolved='on',rfs_review='on',template_approved='on',platform_receipt='on')
        p=self.plan();key=f"container:{p['code']}:1";_,_,basis=m.ASU.container_context(p['code'],1)
        data=dict(entity_key=key,basis=basis,approval_ref='FINAL-C',container_no='C1',container_tare_kg=2000,dunnage_kg=10,securing_kg=10,vgm_kg=2120,vgm_method='sum',line_limit_kg_m=2500,wall_side_kN=0,wall_end_kN=0,vgm_certificate='on',line_load_review='on',wall_load_review='on',dynamic_review='on',lashing_review='on')
        r=self.client.post('/asu/record',data=data,files={'evidence':('final.pdf',VALID_PDF,'application/pdf')},follow_redirects=False);self.assertNotIn('error=',r.headers['location'])
        self.assertEqual(m.ASU.gate(p),[])
    def test_partial_plan_cannot_close_shipment(self):
        self.seed(qty=2);p=self.plan();p['containers'][0]['placements'].pop();self.assertTrue(any('全部运输单元' in r['message'] for r in m.ASU.container_checks(p)))
    def test_profile_edit_during_repack_does_not_commit(self):
        self.seed(package='木箱');original=m._pack_wood_items_to_crates
        def race(*args,**kwargs):
            result=original(*args,**kwargs);m._json_save(self.path,dict(a.DEFAULT,enabled=True,panel_material='solid_wood'));return result
        with patch.object(m,'_pack_wood_items_to_crates',side_effect=race):
            with self.assertRaisesRegex(ValueError,'发生变化'):m.apply_smart_crates(1800,1500,1500,4000,50,45)
        self.assertFalse(m.fetch_state()[1])
    def test_container_vgm_understatement_and_wall_load_rejected(self):
        self.seed();p=self.plan();key=f"container:{p['code']}:1"
        _,b,basis=m.ASU.container_context(p['code'],1)
        self.record_container(key,basis)
        checks=m.ASU.container_checks(p)
        self.assertTrue(any(x['status']=='不满足' and 'VGM' in x['message'] for x in checks));self.assertTrue(any(x['status']=='不满足' and 'wall_side' in x['message'] for x in checks))
    def record_container(self,key,basis):
        r=self.client.post('/asu/record',data=dict(entity_key=key,basis=basis,approval_ref='C-1',container_no='C1',container_tare_kg=2000,dunnage_kg=10,securing_kg=10,vgm_kg=2000,vgm_method='sum',line_limit_kg_m=2500,wall_side_kN=99999,wall_end_kN=0,vgm_certificate='on',line_load_review='on',wall_load_review='on',dynamic_review='on',lashing_review='on'),files={'evidence':('c.pdf',VALID_PDF,'application/pdf')},follow_redirects=False)
        self.assertNotIn('error=',r.headers['location'])
