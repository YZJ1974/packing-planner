import unittest,json
from test_v58_regression import IsolatedDbMixin,m
from fastapi.testclient import TestClient
from app.selected_fleet import CODE
from app import asu_standard

class SelectedFleet(IsolatedDbMixin,unittest.TestCase):
    def setUp(self):
        super().setUp();self.path=m.FLEET.path;self.path.unlink(missing_ok=True);self.client=TestClient(m.app,raise_server_exceptions=True)
    def tearDown(self):self.path.unlink(missing_ok=True);(m.DATA_DIR/'asu_project.json').unlink(missing_ok=True);super().tearDown()
    def seed(self,name='cargo',qty=1,length=5600,width=2100,height=2100,weight=100):
        con=m.db();iid=con.execute('INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,package_type,stackable,rotatable) VALUES(?,?,?,?,?,?,?,0,0)',(name,qty,length,width,height,weight,'裸装')).lastrowid;con.commit();con.close();return iid
    def choose(self,**counts):
        r=self.client.post('/container-selection',data=dict(mode='selected',**{'qty:'+k:v for k,v in counts.items()}),follow_redirects=False);self.assertNotIn('selection_error=',r.headers['location']);return r
    def rec(self):return m.recommend_containers(*m.fetch_state())
    def test_auto_mode_remains_default(self):
        self.seed();r=self.rec();self.assertNotIn(CODE,r['plans']);self.assertFalse(m.FLEET.config()['enabled'])
    def test_exact_selected_fleet_and_full_load(self):
        self.seed(qty=2);self.choose(**{'20GP':2});r=self.rec();p=r['plans'][CODE]
        self.assertEqual([b['container_code'] for b in p['containers']],['20GP','20GP']);self.assertEqual(p['count'],2);self.assertEqual(p['package_count'],2);self.assertFalse(p['partial']);self.assertEqual(len(r['options']),1)
    def test_no_automatic_extra_container_when_insufficient(self):
        self.seed(qty=2);self.choose(**{'20GP':1});r=self.rec();p=r['plans'][CODE]
        self.assertEqual(p['count'],1);self.assertEqual(p['package_count'],1);self.assertEqual(p['unplaced_count'],1);self.assertTrue(p['partial']);self.assertEqual(len(r['oversize']),1)
        self.assertTrue(any('未全部装入' in s for s in m._formal_export_issues(p,CODE)))
    def test_mixed_selection_keeps_types_and_empty_slots(self):
        self.seed(length=500,width=500,height=500);self.choose(**{'20GP':1,'40HQ':1});p=self.rec()['plans'][CODE]
        self.assertEqual([b['container_code'] for b in p['containers']],['20GP','40HQ']);self.assertEqual(p['unused_count'],1);self.assertEqual(p['used_count'],1)
    def test_all_unplaced_still_shows_selected_empty_container(self):
        self.seed(length=8000);self.choose(**{'20GP':1});p=self.rec()['plans'][CODE]
        self.assertEqual(p['count'],1);self.assertEqual(p['package_count'],0);self.assertEqual(p['unused_count'],1);self.assertEqual(p['unplaced_count'],1)
        self.assertEqual(self.client.get('/').status_code,200)
    def test_invalid_counts_do_not_replace_valid_selection(self):
        self.choose(**{'20GP':1});before=self.path.read_bytes()
        for value in ('-1','nan','inf','1.5','abc','101'):
            r=self.client.post('/container-selection',data={'mode':'selected','qty:20GP':value},follow_redirects=False)
            self.assertIn('selection_error=',r.headers['location']);self.assertEqual(self.path.read_bytes(),before)
    def test_empty_selection_rejected(self):
        r=self.client.post('/container-selection',data={'mode':'selected'},follow_redirects=False);self.assertIn('selection_error=',r.headers['location']);self.assertFalse(self.path.exists())
    def test_changed_selection_invalidates_layout_signature_and_plan_cache(self):
        self.seed();self.choose(**{'20GP':1});p=self.rec()['plans'][CODE];sig=m._container_box_signature(p['containers'][0]);self.choose(**{'20GP':2});p2=self.rec()['plans'][CODE]
        self.assertEqual(p2['count'],2);self.assertNotEqual(sig,m._container_box_signature(p2['containers'][0]))
    def test_switch_back_to_auto(self):
        self.seed();self.choose(**{'20GP':1});self.client.post('/container-selection',data={'mode':'auto'});self.assertFalse(m.FLEET.config()['enabled']);self.assertNotIn(CODE,self.rec()['plans'])
    def test_missing_selected_code_does_not_fallback(self):
        self.seed();m._json_save(self.path,{'enabled':True,'fleet':[{'code':'MISSING','count':1}]});r=self.rec();self.assertFalse(r['options']);self.assertIn('MISSING',r['container_config_error']);self.assertEqual(self.client.get('/').status_code,200)
    def test_corrupt_selection_can_be_recovered_explicitly(self):
        self.seed();self.path.write_text('{broken');self.assertFalse(self.rec()['options']);self.assertEqual(self.client.get('/').status_code,200)
        self.client.post('/container-selection',data={'mode':'auto'});self.assertTrue(self.rec()['options'])
    def test_asu_clearance_remains_active(self):
        m._json_save(m.DATA_DIR/'asu_project.json',dict(asu_standard.DEFAULT,enabled=True));self.seed(length=5800);self.choose(**{'20GP':1});p=self.rec()['plans'][CODE];self.assertEqual(p['unplaced_count'],1)
    def test_manual_validation_and_planning_export_use_selected_scheme(self):
        self.seed();self.choose(**{'20GP':1});p=self.rec()['plans'][CODE];b=p['containers'][0]
        r=self.client.post('/container-layout/validate',json={'scheme':CODE,'container_index':1,'placements':b['placements']});self.assertEqual(r.status_code,200);self.assertTrue(r.json()['ok'])
        r=self.client.post('/export-container-svg',json={'scheme':CODE,'container_index':1,'mode':'planning'});self.assertEqual(r.status_code,200);self.assertIn('20GP',r.text)
    def test_duplicate_names_are_not_lost_from_unloaded_list(self):
        self.seed('same');self.seed('same');self.choose(**{'20GP':1});r=self.rec();rows,_=m._unloaded_packing_records(*m.fetch_state(),r,CODE);self.assertEqual(len(rows),1)
    def test_no_material_dimensions_weights_or_quantities_modified(self):
        self.seed(qty=2);m.fetch_state();con=m.db();before=[tuple(r) for r in con.execute('SELECT * FROM items')];con.close();self.choose(**{'20GP':1});self.rec();con=m.db();after=[tuple(r) for r in con.execute('SELECT * FROM items')];con.close();self.assertEqual(before,after)
    def test_selection_controls_and_partial_list_render(self):
        self.seed(qty=2);self.choose(**{'20GP':1});r=self.client.get('/');self.assertEqual(r.status_code,200);self.assertIn('只按我选择的集装箱排箱',r.text);self.assertIn('所选柜未全部装下',r.text)
