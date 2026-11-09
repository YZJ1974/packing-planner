import unittest,copy,json
from xml.etree import ElementTree as ET
from test_v58_regression import IsolatedDbMixin,m,VALID_PDF
from fastapi.testclient import TestClient
from app import trucks as t,asu_standard as a

class TruckTests(IsolatedDbMixin,unittest.TestCase):
    def setUp(self):
        super().setUp();self.set_user();self.client=TestClient(m.app,raise_server_exceptions=True)
        for name in ('truck_settings.json','selected_trucks.json','selected_containers.json','asu_project.json'):(m.DATA_DIR/name).unlink(missing_ok=True)
    def tearDown(self):
        for name in ('truck_settings.json','selected_trucks.json','selected_containers.json','asu_project.json'):(m.DATA_DIR/name).unlink(missing_ok=True)
        super().tearDown()
    def save(self,proof=False,**kw):
        d=dict(code='TRUCK_6M8',label='Test flatbed',enabled='on',body_type='flatbed',length_mm=6800,width_mm=2400,cargo_height_mm=2500,payload_kg=8000,door_width_mm=2400,door_height_mm=2500,clearance_mm=0,use_truck='on',reference='VEHICLE-01');d.update(kw)
        args={'data':d,'follow_redirects':False}
        if proof:args['files']={'evidence':('vehicle.pdf',VALID_PDF,'application/pdf')}
        r=self.client.post('/vehicles/save',**args);self.assertEqual(r.status_code,303);self.assertNotIn('error=',r.headers['location']);return r
    def seed(self,qty=1,length=2000,width=1000,height=1000,weight=1000):
        con=m.db();iid=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,package_type,stackable,rotatable) VALUES('Truck cargo',?,?,?,?,?,'裸装',0,0)",(qty,length,width,height,weight)).lastrowid;con.commit();con.close();return iid
    def plan(self):
        rec=m.recommend_containers(*m.fetch_state());self.assertTrue(rec['options'],rec);return rec['options'][0]
    def model(self):
        r=t.defaults()['vehicles'][0];r.update(enabled=True,payload_kg=8000,deck_height_mm=1200,empty_height_mm=3000,route_height_mm=4000,support_a_x_mm=1000,support_b_x_mm=5000,empty_a_kg=3000,empty_b_kg=4000,limit_a_kg=5000,limit_b_kg=10000,max_gross_kg=15000);return t.as_carrier(r)
    def cargo(self,x=0,weight=7000):return dict(name='heavy',x=x,y=0,z=0,l=2000,w=1000,h=1000,weight_kg=weight,cg_local_x=1000)
    def test_templates_have_no_invented_payload(self):
        self.assertTrue(all(r['payload_kg'] is None and not r['enabled'] for r in t.defaults()['vehicles']));self.assertEqual(m.TRUCKS.mode(),'container')
    def test_mode_without_vehicle_opens_editor(self):
        r=self.client.post('/vehicle-mode',data={'mode':'truck'},follow_redirects=False);self.assertTrue(r.headers['location'].startswith('/vehicles'));self.assertEqual(m.TRUCKS.mode(),'container')
    def test_flatbed_and_box_have_distinct_door_rules(self):
        self.save(door_width_mm=100,door_height_mm=100);c=m._active_containers()[0];p=dict(length_mm=2000,width_mm=1000,height_mm=1000,weight_kg=1000,rotatable=False)
        self.assertTrue(m.fits_container(p,c));self.assertFalse(m.door_maneuver_audit([self.cargo(weight=1000)],c)['checked'])
        self.save(body_type='box',door_width_mm=100,door_height_mm=100);self.assertFalse(m.fits_container(p,m._active_containers()[0]))
    def test_flatbed_never_inherits_open_top_unlimited_height(self):
        self.save();c=m._active_containers()[0];p=dict(name='too tall',length_mm=1000,width_mm=1000,height_mm=2600,weight_kg=100,rotatable=False)
        self.assertFalse(m.fits_container(p,c));self.assertIsNone(m._can_place(p,(1000,1000,2600),(0,0,0),m._new_bin_state(c),c))
    def test_asu_container_gap_not_applied_to_truck(self):
        self.save();m._json_save(m.DATA_DIR/'asu_project.json',dict(a.DEFAULT,enabled=True));c=m._active_containers()[0]
        p=dict(length_mm=6800,width_mm=2400,height_mm=1000,weight_kg=100,rotatable=False);self.assertTrue(m.fits_container(p,c));self.assertEqual(m._asu_margin(c),0)
        self.assertEqual(m._asu_margin(m.DEFAULT_CONTAINERS[0]),100)
    def test_configured_truck_gap_applies_without_asu(self):
        self.save(clearance_mm=50);c=m._active_containers()[0];self.assertEqual(m._asu_margin(c),50);p=dict(length_mm=6750,width_mm=2200,height_mm=500,weight_kg=100,rotatable=False);self.assertFalse(m.fits_container(p,c))
    def test_route_height_restricts_cargo_height(self):
        self.save(deck_height_mm=1200,empty_height_mm=3000,route_height_mm=3500);c=m._active_containers()[0];self.assertEqual(c['inner_h'],2300)
        p=dict(length_mm=1000,width_mm=1000,height_mm=2400,weight_kg=100,rotatable=False);self.assertFalse(m.fits_container(p,c))
    def test_static_reactions_conserve_weight_and_moment(self):
        c=self.model();p=self.cargo(x=2400);r=t.axle_loads(c,[p]);self.assertAlmostEqual(r['cargo_a_kg']+r['cargo_b_kg'],7000);self.assertAlmostEqual(r['loaded_a_kg']+r['loaded_b_kg'],14000)
        self.assertAlmostEqual(r['cargo_a_kg']*1000+r['cargo_b_kg']*5000,7000*3400)
        self.assertTrue(any('支承组A' in x for x in t.audit(c,[p])['hard']))
    def test_axle_aware_translation_preserves_geometry_and_solves_static_limits(self):
        c=self.model();p=self.cargo();st={'container':c,'placements':[p],'points':[(0,0,0)],'weight':7000};before=(p['l'],p['w'],p['h'],p['weight_kg']);m._center_container_state(st)
        self.assertFalse(t.audit(c,st['placements'])['hard']);self.assertGreaterEqual(p['x'],0);self.assertLessEqual(p['x']+p['l'],6800)
        self.assertEqual(before,(p['l'],p['w'],p['h'],p['weight_kg']))
    def test_effective_payload_respects_gross_and_group_capacity(self):
        row=self.model()['truck_parameters'];row['max_gross_kg']=13000;self.assertEqual(t.as_carrier(row)['payload'],6000)
    def test_missing_axles_are_review_not_fake_pass(self):
        self.save();c=m._active_containers()[0];r=t.audit(c,[self.cargo(weight=1000)]);self.assertIsNone(r['axle']);self.assertTrue(any('未进行静态轴荷' in s for s in r['review']))
    def test_reference_or_parameters_change_invalidates_vehicle_proof(self):
        self.save(proof=True);self.assertTrue(m.TRUCKS.verified(m._active_containers()[0]));self.save(reference='OTHER-VEHICLE');self.assertFalse(m.TRUCKS.verified(m._active_containers()[0]))
    def test_invalid_parameters_do_not_overwrite_saved_vehicle(self):
        self.save();old=m.TRUCKS.path.read_bytes()
        for v in ('nan','inf','-1','0'):
            r=self.client.post('/vehicles/save',data={'code':'TRUCK_6M8','enabled':'on','body_type':'flatbed','length_mm':6800,'width_mm':2400,'cargo_height_mm':2500,'payload_kg':v},follow_redirects=False);self.assertEqual(r.status_code,422);self.assertEqual(old,m.TRUCKS.path.read_bytes())
    def test_partial_support_inputs_rejected(self):
        row=self.model()['truck_parameters'];row['empty_b_kg']=None
        with self.assertRaisesRegex(ValueError,'完整填写'):t.validate(row)
    def test_support_coordinates_may_be_outside_deck(self):
        row=self.model()['truck_parameters'];row.update(support_a_x_mm=-1000,support_b_x_mm=9000);self.assertEqual(t.validate(row)['support_a_x_mm'],-1000)
    def test_separate_truck_and_container_selections(self):
        self.client.post('/container-selection',data={'mode':'selected','qty:20GP':2});self.save();self.client.post('/container-selection',data={'mode':'selected','qty:TRUCK_6M8':1});self.assertEqual(m.FLEET.config()['fleet'][0]['code'],'TRUCK_6M8')
        self.client.post('/vehicle-mode',data={'mode':'container'});self.assertEqual(m.FLEET.config()['fleet'],[{'code':'20GP','count':2}])
    def test_selected_truck_partial_load_and_vehicle_metadata(self):
        self.save(payload_kg=1500);self.seed(qty=2);self.client.post('/container-selection',data={'mode':'selected','qty:TRUCK_6M8':1});p=self.plan();self.assertEqual(p['code'],'TRUCK_SELECTED');self.assertEqual(p['count'],1);self.assertEqual(p['unplaced_count'],1);self.assertEqual(p['containers'][0]['vehicle_type'],'truck')
    def test_view_editor_and_road_dossier_render(self):
        self.save();self.seed();p=self.plan();r=self.client.get('/');self.assertEqual(r.status_code,200);self.assertIn('平板，无车门限制',r.text)
        self.assertEqual(self.client.get('/vehicles').status_code,200)
        r=self.client.get('/asu-project?entity=container:'+p['code']+':1');self.assertEqual(r.status_code,200,r.text[:300]);self.assertIn('货车本次装载与道路资料',r.text);self.assertNotIn('name="vgm_kg"',r.text)
    def test_formal_truck_export_does_not_request_container_certificate(self):
        self.save();self.seed();p=self.plan();issues=m._formal_export_issues(p,p['code']);self.assertTrue(any('实车参数' in s for s in issues));self.assertFalse(any('标准柜规划' in s or 'VGM' in s for s in issues))
    def test_planning_svg_and_manual_validation_for_truck(self):
        self.save();self.seed();p=self.plan();b=p['containers'][0]
        r=self.client.post('/container-layout/validate',json={'scheme':p['code'],'container_index':1,'placements':b['placements']});self.assertTrue(r.json()['ok'])
        r=self.client.post('/export-container-svg',json={'scheme':p['code'],'container_index':1,'mode':'planning'});self.assertEqual(r.status_code,200);ET.fromstring(r.content);self.assertIn('TRUCK_6M8',r.text)
    def test_corrupt_truck_config_is_repairable_without_losing_materials(self):
        self.seed();m.TRUCKS.path.write_text('{broken');self.assertEqual(self.client.get('/').status_code,409);self.assertEqual(self.client.get('/vehicles').status_code,200)
        self.client.post('/vehicles/reset');self.assertEqual(m.TRUCKS.mode(),'container');self.assertEqual(len(m.fetch_state()[0]),1)
    def test_truck_edits_invalidate_layout_signature(self):
        self.save();self.seed();b=self.plan()['containers'][0];old=m._container_box_signature(b);self.save(width_mm=2500);self.assertNotEqual(old,m._container_box_signature(self.plan()['containers'][0]))
    def test_actual_vehicle_fields_required_for_formal_without_asu(self):
        self.save();self.seed();p=self.plan();checks=m.ASU.container_checks(p);self.assertTrue(any('静态支承组' in r['message'] for r in checks));self.assertTrue(any('路线高度' in r['message'] for r in checks))
    def test_road_dossier_can_close_without_vgm_or_sea_walls(self):
        params={k:v for k,v in self.model()['truck_parameters'].items() if k in t.FIELDS};self.save(proof=True,**params);self.seed();p=self.plan();key='container:'+p['code']+':1';_,_,basis=m.ASU.container_context(p['code'],1)
        r=self.client.post('/asu/record',data={'entity_key':key,'basis':basis,'approval_ref':'ROAD-01','dynamic_review':'on','lashing_review':'on','line_load_review':'on','rfs_review':'on'},files={'evidence':('road.pdf',VALID_PDF,'application/pdf')},follow_redirects=False)
        self.assertNotIn('error=',r.headers['location']);self.assertEqual(m.ASU.container_checks(p),[])
    def test_load_list_uses_truck_template_and_preserves_original(self):
        self.save();self.seed();p=self.plan();before=m.CONTAINER_LOAD_TEMPLATE_PATH.read_bytes()
        raw=t.load_template(before,p);info=m._xlsx_export_header_info(raw,m.CONTAINER_EXPORT_ALIASES);self.assertIn('container_type',info['mapped']);self.assertIn('container_no',info['mapped'])
        self.assertEqual(before,m.CONTAINER_LOAD_TEMPLATE_PATH.read_bytes())
        r=self.client.post('/export-container-load-list',json={'scheme':p['code'],'container_index':0,'mode':'planning'});self.assertEqual(r.status_code,200,r.text[:200])
    def test_impossible_empty_vehicle_heights_rejected(self):
        row=self.model()['truck_parameters'];row['empty_height_mm']=500
        with self.assertRaisesRegex(ValueError,'低于货台'):t.validate(row)
