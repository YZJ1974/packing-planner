import unittest,copy
from unittest.mock import patch
from xml.etree import ElementTree as ET
from test_v58_regression import IsolatedDbMixin,m
from app.word_report import report_svg,cargo_rows
from fastapi.testclient import TestClient

class WordLayoutExportTests(IsolatedDbMixin,unittest.TestCase):
    def box(self,index=1):
        return dict(index=index,container_code='40HQ',inner_l=12032,inner_w=2352,inner_h=2698,payload=28600,total_weight=150,volume_utilization=10,weight_utilization=1,engineering_audit={'status':'需复核'},placements=[dict(seq=1,asu_key='item:1',name='分子筛-1',x=100,y=100,z=0,l=600,w=600,h=1000,weight_kg=150,type='铁桶',display_shape='upright_cylinder',color='#10a080')])
    def plan(self):return dict(code='40HQ',label='40HQ',count=2,containers=[self.box(1),self.box(2)])
    def setup_route(self,plan=None):
        plan=plan or self.plan()
        a=patch.object(m,'recommend_containers',return_value={'plans':{'40HQ':plan}});b=patch.object(m,'_apply_saved_container_layouts',side_effect=lambda r:r)
        a.start();b.start();self.addCleanup(a.stop);self.addCleanup(b.stop)
        return TestClient(m.app)
    def test_actual_geometry_overheight_is_explicit_even_without_audit_flag(self):
        from app.word_report import overheight_warning
        b=self.box();b['inner_h']=2350;b['placements'][0]['h']=3100
        self.assertIn('顶部超高 750 mm',overheight_warning(b));self.assertIn('未确认可装运',overheight_warning(b))
        b['inner_h']=2698;b['placements'][0]['h']=2220;self.assertEqual(overheight_warning(b),'')
    def test_all_four_views_are_clean_and_geometry_is_not_mutated(self):
        b=self.box();before=copy.deepcopy(b)
        for view in ['perspective','top','front','side']:
            raw=report_svg(b,view);root=ET.fromstring(raw)
            self.assertIn(b'viewBox',raw);self.assertIn(b'upright-cylinder',raw)
            self.assertNotIn('吊钩',raw.decode());self.assertNotIn('吊点',raw.decode());self.assertNotIn('吊索',raw.decode());self.assertNotIn(b'#db7c19',raw)
            self.assertFalse(any(n.tag.rsplit('}',1)[-1]=='circle' for n in root.iter()))
        self.assertEqual(b,before)
    def test_cargo_rows_keep_quantity_and_mass_and_do_not_mix_sources(self):
        b=self.box();b['placements'] += [dict(b['placements'][0],seq=2,name='分子筛-2'),dict(b['placements'][0],seq=3,asu_key='item:2',name='其他材料')]
        b['flexible_fill']=[dict(name='珠光砂',quantity=10,weight_kg=70,volume_m3=1.25)]
        rows=cargo_rows(b)
        self.assertEqual(len(rows),3);self.assertEqual(rows[0][3],'2');self.assertEqual(rows[0][-1],'300.0')
        self.assertEqual(sum(float(r[-1].replace(',','')) for r in rows),520)
        self.assertEqual(rows[-1][3],'10');self.assertIn('1.250 m³',rows[-1][4])
    def test_selected_scheme_exports_every_container_and_ignores_lifting_markup(self):
        client=self.setup_route();captured={}
        def build(job):captured.update(job);return b'PK-test-word'
        with patch.object(m,'_build_layout_word_bytes',side_effect=build):
            r=client.post('/export-container-layout-word',json=dict(scheme='40HQ',container_index=1,mode='planning',lifting={'enabled':True},kind='lifting',html='<script>bad</script>'))
        self.assertEqual(r.status_code,200);self.assertIn('wordprocessingml',r.headers['content-type']);self.assertIn('.docx',r.headers['content-disposition'])
        self.assertEqual(len(captured['plan']['containers']),2);self.assertNotIn('lifting',captured);self.assertNotIn('html',captured)
        self.assertTrue(captured['planning_only']);self.assertEqual(len(captured['snapshot_id']),64)
        self.assertTrue(any(p.suffix=='.docx' for p in m.EXPORT_DIR.glob('Container_Layout_Word_*')))
    def test_dirty_or_invalid_manual_layout_blocks_report(self):
        client=self.setup_route()
        with patch.object(m,'_draft_rows',return_value=[{'dirty':1}]),patch.object(m,'_build_layout_word_bytes') as build:
            self.assertEqual(client.post('/export-container-layout-word',json={'scheme':'40HQ'}).status_code,409);build.assert_not_called()
        client=self.setup_route(dict(self.plan(),invalid_manual_layouts=[1]))
        self.assertEqual(client.post('/export-container-layout-word',json={'scheme':'40HQ'}).status_code,409)
    def test_formal_mode_keeps_existing_release_gates(self):
        client=self.setup_route()
        with patch.object(m,'_formal_export_issues',return_value=['袋装仅规划']),patch.object(m,'_build_layout_word_bytes') as build:
            r=client.post('/export-container-layout-word',json={'scheme':'40HQ','mode':'formal'})
            self.assertEqual(r.status_code,409);build.assert_not_called()
    def test_invalid_scheme_mode_or_payload_rejected_before_worker(self):
        client=self.setup_route()
        for data in [dict(scheme='missing'),dict(scheme='40HQ',mode='unknown'),[]]:
            with patch.object(m,'_build_layout_word_bytes') as build:
                self.assertEqual(client.post('/export-container-layout-word',json=data).status_code,422);build.assert_not_called()
