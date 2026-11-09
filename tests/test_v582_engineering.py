import unittest
from test_v58_regression import IsolatedDbMixin, m
from app.physical_constraints import support_load_analysis, validate_contact_geometry, floor_load_audit, _balanced_reactions

def box(x=0,z=0,l=1000,weight=100,**kw):
    p=dict(name='B',x=x,y=0,z=z,l=l,w=1000,h=100,weight_kg=weight,stackable=True,type='木箱',top_load_verified=True,max_top_load_kg=10000)
    p.update(kw);return p

class TestSupport(unittest.TestCase):
    def test_outside_cg_rejected_at_75_percent_support(self):
        self.assertFalse(m._support_ok(box(z=100,cg_local_x=950),[box(l=750)]))
    def test_balanced_overhang_allowed(self):
        self.assertTrue(m._support_ok(box(z=100,cg_local_x=500),[box(l=750)]))
    def test_support_hull_not_bounding_rectangle(self):
        self.assertIsNone(_balanced_reactions([(0,0),(1000,0),(0,1000)],(800,800)))
    def test_reactions_balance_force_and_moments(self):
        r=_balanced_reactions([(0,0),(0,1000),(1000,0),(1000,1000)],(750,200))
        self.assertAlmostEqual(sum(w for p,w in r),1)
        for axis,t in enumerate((750,200)):self.assertAlmostEqual(sum(p[axis]*w for p,w in r),t)
        self.assertTrue(all(w>=0 for p,w in r))
    def test_upper_load_tips_middle_box(self):
        base=box(l=750,weight=10);mid=box(z=100,weight=10,cg_local_x=500)
        self.assertTrue(m._support_ok(mid,[base]))
        self.assertFalse(m._support_ok(box(z=200,weight=1000,cg_local_x=950),[base,mid]))
    def test_eccentric_load_uses_conservative_bounds(self):
        left=box(l=500,max_top_load_kg=600);right=box(x=500,l=500,max_top_load_kg=600);top=box(z=100,weight=1000,cg_local_x=750)
        a=support_load_analysis([left,right,top])
        self.assertEqual(a['carried'][id(left)],1000);self.assertEqual(a['carried'][id(right)],1000)
        self.assertTrue(a['engineering_reviews']);self.assertFalse(m._support_ok(top,[left,right]))
    def test_shared_ancestor_counted_once(self):
        base=box();left=box(z=100,l=500);right=box(x=500,z=100,l=500);top=box(z=200,weight=1000)
        self.assertEqual(support_load_analysis([base,left,right,top])['carried'][id(base)],1200)
    def test_floating_package_fails(self):
        self.assertTrue(support_load_analysis([box(z=100)])['hard_issues'])
    def test_bad_cg_fails(self):
        for cg in (float('nan'),float('inf'),-1,1001):
            self.assertTrue(support_load_analysis([box(),box(z=100,cg_local_x=cg)])['hard_issues'])
    def test_manual_layout_rechecked(self):
        self.assertTrue(any('支撑' in x for x in m._container_physical_issues({'container_code':'20GP','placements':[box(l=750),box(z=100,cg_local_x=950)]})))

class TestArea(unittest.TestCase):
    def test_bad_custom_area_rejected(self):
        for value in (20,float('nan'),float('inf'),-1,0):
            with self.assertRaises(ValueError):validate_contact_geometry(box(contact_area_mode='CUSTOM',custom_contact_area_m2=value))
    def test_exact_footprint_allowed(self):
        validate_contact_geometry(box(contact_area_mode='CUSTOM',custom_contact_area_m2=1))
    def test_invalid_skids_rejected(self):
        for n,w,l in ((2,600,1000),(2,100,1001),(1.5,100,500)):
            with self.assertRaises(ValueError):validate_contact_geometry(box(contact_area_mode='SKIDS',skid_count=n,skid_width_mm=w,skid_contact_length_mm=l))
    def test_rotated_skid_geometry_allowed(self):
        validate_contact_geometry(box(l=1000,w=2000,contact_area_mode='SKIDS',skid_count=2,skid_width_mm=100,skid_contact_length_mm=2000))
    def test_bad_stored_area_fails_without_floor_limit(self):
        self.assertTrue(floor_load_audit([box(contact_area_mode='CUSTOM',custom_contact_area_m2=20)],{})['hard_issues'])
    def test_skid_pressure_formula(self):
        self.assertEqual(m.floor_contact_pressure_kg_m2(box(l=2000,weight=4000,contact_area_mode='SKIDS',skid_count=2,skid_width_mm=100,skid_contact_length_mm=2000)),10000)

class TestIntegration(IsolatedDbMixin,unittest.TestCase):
    def seed_crate(self):
        con=m.db();cid=con.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg) VALUES('TEST',2000,1000,800,100)").lastrowid;con.commit();con.close();return cid
    def test_invalid_api_input_does_not_save(self):
        from fastapi.testclient import TestClient
        cid=self.seed_crate()
        r=TestClient(m.app).post(f'/crates/{cid}/physical',data={'contact_area_mode':'CUSTOM','contact_area_source_type':'FIELD_MEASURED','custom_contact_area_m2':'20'},follow_redirects=False)
        self.assertIn('error=',r.headers['location'])
        con=m.db();row=dict(con.execute('SELECT * FROM crates WHERE id=?',(cid,)).fetchone());con.close()
        self.assertNotEqual(row.get('contact_area_mode'),'CUSTOM')
    def test_legacy_pass_rechecked_without_moving_cargo(self):
        cid=self.seed_crate();con=m.db()
        con.execute("INSERT INTO crate_audits(crate_id,audit_status,conclusion,support_ok) VALUES(?,'通过','legacy pass',1)",(cid,))
        for seq,p in enumerate([box(l=750),box(z=100,cg_local_x=950)],1):
            con.execute('INSERT INTO crate_placements(crate_id,seq,item_name,x,y,z,l,w,h,weight_kg,cg_local_x) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(cid,seq,'B',p['x'],0,p['z'],p['l'],1000,100,p['weight_kg'],p.get('cg_local_x')))
        con.commit();con.close();m.fetch_state();con=m.db()
        a=dict(con.execute('SELECT * FROM crate_audits WHERE crate_id=?',(cid,)).fetchone());zs=[r[0] for r in con.execute('SELECT z FROM crate_placements WHERE crate_id=? ORDER BY seq',(cid,))];con.close()
        self.assertEqual(a['audit_status'],'不通过');self.assertEqual(zs,[0,100])
    def test_multi_support_requires_engineering_review(self):
        pls=[box(l=500),box(x=500,l=500),box(z=100)]
        self.assertTrue(m.top_load_audit(pls)['engineering_reviews'])
    def test_wording_is_search_bounded(self):
        import inspect
        self.assertIn('当前搜索找到的最少柜数',inspect.getsource(m._recommend_containers_core))

if __name__=='__main__':unittest.main(verbosity=2)
