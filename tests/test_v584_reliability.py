import json,math,unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from test_v58_regression import IsolatedDbMixin,m
from fastapi.testclient import TestClient
from app.svg_exports import render_svg

class Reliability(IsolatedDbMixin,unittest.TestCase):
    def setUp(self):super().setUp();self.client=TestClient(m.app,raise_server_exceptions=False)
    def item(self,name='设备甲',**kw):
        data=dict(name=name,qty=1,length_mm=800,width_mm=600,height_mm=500,weight_kg=100,package_type='裸装',stackable=0,rotatable=1);data.update(kw)
        r=self.client.post('/items',data=data);self.assertEqual(r.status_code,200,r.text)
        con=m.db();iid=con.execute('SELECT max(id) FROM items').fetchone()[0];con.close();return iid
    def prepacked(self):
        row=dict(name='已包装设备',qty=1,length_mm=1200,width_mm=1000,height_mm=1000,weight_kg=200,package_type='木箱',stackable=0,rotatable=1,box_no='R584')
        m.insert_imported_shipping_rows([row]);return m.fetch_state()
    def plan(self):
        items,crates=m.fetch_state();rec=m.recommend_containers(items,crates);p=rec['options'][0];return p,p['containers'][0]
    def test_import_semantics_and_export_net_unknown(self):
        items,crates=self.prepacked();c=crates[0];st=m.crate_stats(c,items)
        self.assertEqual(c['package_origin'],'PREPACKED');self.assertEqual(items[0]['is_package_placeholder'],1)
        self.assertFalse(st['internal_metrics_available']);self.assertEqual(st['gross_weight'],200)
        record=m._packing_list_records(items,crates)[0];self.assertEqual(record['net_weight'],'');self.assertEqual(record['gross_weight'],200)
        html=self.client.get('/');self.assertEqual(html.status_code,200);self.assertIn('净重、箱皮与箱内利用率未知',html.text)
        self.assertNotIn('253.3%',html.text)
    def test_global_reboxing_retains_prepacked_rows_and_dimensions(self):
        items,crates=self.prepacked();c=crates[0];iid=items[0]['id']
        self.item('待装箱设备',package_type='木箱',length_mm=300,width_mm=300,height_mm=300,weight_kg=20)
        m.apply_smart_crates(1800,1500,1500,4000,50,45)
        items2,crates2=m.fetch_state();retained=next(q for q in crates2 if q['id']==c['id'])
        self.assertEqual(retained,c);self.assertTrue(any(q['id']==iid and q['is_package_placeholder'] for q in items2))
        self.assertTrue(any(q['id']!=c['id'] for q in crates2))
    def test_prepacked_cannot_be_automatically_repacked(self):
        items,crates=self.prepacked()
        with self.assertRaises(ValueError):m._execute_smart_fill(crates[0]['id'])
    def test_prepacked_cg_coordinates_are_package_cg(self):
        items,crates=self.prepacked();iid=items[0]['id']
        r=self.client.post(f'/items/{iid}/cg',data={'cg_x_mm':900,'cg_y_mm':400,'cg_z_mm':300,'cg_source_type':'MANUFACTURER_DATA'},follow_redirects=False)
        self.assertNotIn('error=',r.headers['location']);pk=m.get_packages(*m.fetch_state())[0];self.assertEqual(pk['cg_local_x'],900)
    def test_negative_quantity_rejected_without_insert(self):
        r=self.client.post('/items',data=dict(name='非法',qty=-2,length_mm=100,width_mm=100,height_mm=100,weight_kg=100,package_type='裸装'))
        self.assertEqual(r.status_code,422);self.assertFalse(m.fetch_state()[0])
    def test_zero_dimension_and_infinite_weight_rejected(self):
        for value in ('0','inf','nan','-1'):
            r=self.client.post('/crates',data={'name':'无效','length_mm':value,'width_mm':100,'height_mm':100})
            self.assertEqual(r.status_code,422,r.text)
    def test_nonfinite_topload_cannot_be_confirmed(self):
        items,crates=self.prepacked();cid=crates[0]['id']
        r=self.client.post(f'/crates/{cid}/physical',data={'max_top_load_kg':'inf','top_load_confirmed':1,'top_load_source_type':'ENGINEERING_CALC'},follow_redirects=False)
        self.assertIn('error=',r.headers['location']);self.assertIsNone(m.fetch_state()[1][0]['max_top_load_kg'])
    def test_nonfinite_cg_cannot_be_confirmed(self):
        iid=self.item()
        r=self.client.post(f'/items/{iid}/cg',data={'cg_x_mm':'nan','cg_y_mm':100,'cg_z_mm':100,'cg_source_type':'MANUFACTURER_DATA'},follow_redirects=False)
        self.assertIn('error=',r.headers['location']);self.assertEqual(m.fetch_state()[0][0]['cg_confirmed'],0)
    def test_invalid_replace_import_does_not_delete_old_data(self):
        self.item()
        with self.assertRaises(ValueError):m.insert_imported_shipping_rows([dict(qty=-2,length_mm=100,width_mm=100,height_mm=100,weight_kg=1)],mode='replace')
        self.assertEqual(len(m.fetch_state()[0]),1)
    def test_embedded_json_does_not_break_out_of_script(self):
        name='审查</script><div id=review-marker>惰性标记</div><script type=application/json>'
        self.item(name);html=self.client.get('/').text
        self.assertNotIn('<div id=review-marker>',html)
        import re
        raw=re.search(r'<script id="layout-data"[^>]*>(.*?)</script>',html,re.S).group(1)
        data=json.loads(raw);self.assertIn(name,[q['name'] for p in data.values() for b in p['containers'] for q in b['placements']])
    def test_preview_does_not_write_saved_layouts(self):
        self.item();p,b=self.plan()
        r=self.client.post('/container-layout/validate',json={'scheme':p['code'],'container_index':b['index'],'placements':b['placements']})
        self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['ok'])
        con=m.db();self.assertEqual(con.execute('SELECT count(*) FROM container_manual_layouts').fetchone()[0],0);con.close()
    def test_preview_rejects_nonfinite_and_duplicate_coordinates(self):
        self.item();p,b=self.plan();pls=b['placements']
        for positions in ([dict(pls[0],x='nan')],pls+pls):
            r=self.client.post('/container-layout/validate',json={'scheme':p['code'],'container_index':b['index'],'placements':positions});self.assertFalse(r.json()['ok'])
    def test_svg_planning_always_has_embedded_label(self):
        self.item();p,b=self.plan();r=self.client.post('/export-container-svg',json={'scheme':p['code'],'container_index':b['index'],'view':'top','mode':'planning'})
        self.assertEqual(r.status_code,200,r.text);ET.fromstring(r.content);self.assertIn('DRAFT',r.text);self.assertIn('非签证依据',r.text)
    def test_svg_formal_gate_cannot_be_bypassed(self):
        self.item();p,b=self.plan();r=self.client.post('/export-container-svg',json={'scheme':p['code'],'container_index':b['index'],'mode':'formal'})
        self.assertEqual(r.status_code,409)
    def test_svg_blocks_server_dirty_drafts(self):
        self.item();p,b=self.plan();self.client.post('/container-layout/dirty',json={'scheme':p['code'],'container_index':b['index']})
        r=self.client.post('/export-container-svg',json={'scheme':p['code'],'container_index':b['index'],'mode':'planning'});self.assertEqual(r.status_code,409)
    def test_svg_ignores_client_supplied_markup(self):
        self.item();p,b=self.plan();r=self.client.post('/export-container-svg',json={'scheme':p['code'],'container_index':b['index'],'svg':'<script>untrusted_marker</script>'})
        self.assertEqual(r.status_code,200);self.assertNotIn('untrusted_marker',r.text)
    def test_lifting_export_keeps_not_for_construction_label(self):
        self.item(weight_kg=1000);p,b=self.plan();r=self.client.post('/export-container-svg',json={'scheme':p['code'],'container_index':b['index'],'kind':'lifting','lifting':{'angle':60,'setback':10,'minWeight':500}})
        self.assertEqual(r.status_code,200,r.text);self.assertIn('非吊装施工依据',r.text);self.assertIn('吊钩Z',r.text)
    def test_shared_audit_preserves_results(self):
        self.item();p,b=self.plan();pls=b['placements'];c=m.DEFAULT_CONTAINERS[0];analysis=m.support_load_analysis(pls)
        self.assertEqual(m.top_load_audit(pls),m.top_load_audit(pls,analysis));self.assertEqual(m.floor_load_audit(pls,c),m.floor_load_audit(pls,c,analysis))
    def test_complete_audit_builds_support_graph_once(self):
        self.item();p,b=self.plan()
        with patch.object(m,'support_load_analysis',wraps=m.support_load_analysis) as spy:
            m._container_state_audit({'container':m.DEFAULT_CONTAINERS[0],'placements':b['placements']})
            self.assertEqual(spy.call_count,1)

    def test_legacy_migration_marks_packages_and_invalidates_false_confirmation(self):
        con=m.db()
        cid=con.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,actual_gross_weight_kg,tare_weight_source,top_load_confirmed,max_top_load_kg) VALUES('旧箱',1200,1000,1000,200,'原始箱单毛重',1,?)",(float('inf'),)).lastrowid
        con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,package_type,crate_id,cg_confirmed,cg_x_mm,cg_y_mm,cg_z_mm) VALUES('旧占位',1,1200,1000,1000,200,'木箱',?,1,NULL,200,200)",(cid,))
        con.execute('ALTER TABLE crates DROP COLUMN package_origin');con.execute('ALTER TABLE items DROP COLUMN is_package_placeholder');con.commit();con.close()
        m.init_db();items,crates=m.fetch_state()
        self.assertEqual(crates[0]['package_origin'],'PREPACKED');self.assertEqual(items[0]['is_package_placeholder'],1)
        self.assertEqual(items[0]['cg_confirmed'],0);self.assertEqual(crates[0]['top_load_confirmed'],0)
        self.assertEqual(crates[0]['actual_gross_weight_kg'],200);self.assertEqual(items[0]['weight_kg'],200)

    def test_zero_padding_is_preserved(self):
        self.assertEqual(m._padding_setting({'padding_mm':0}),0)
        self.assertEqual(m._padding_setting({}),100)
    def test_saved_layout_statistics_use_saved_padding(self):
        self.item(package_type='木箱',length_mm=500,width_mm=400,height_mm=300,weight_kg=150)
        m.apply_smart_crates(1800,1500,1500,4000,50,45)
        items,crates=m.fetch_state();self.assertEqual(crates[0]['layout_padding_mm'],50)
        st=m.crate_stats(crates[0],items)
        self.assertLessEqual(st['net_utilization'],100)
