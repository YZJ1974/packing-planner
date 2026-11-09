import unittest,copy
from test_v58_regression import m,IsolatedDbMixin
from app import flexible_fill as f

class FlexibleFillTests(IsolatedDbMixin,unittest.TestCase):
    def bags(self,n=100):return [dict(name='soft-'+str(i),length_mm=560,width_mm=560,height_mm=800,weight_kg=7,type='编织袋',stackable=False,rotatable=True,void_fill=True,flexible_unit_volume_m3=.125,asu_key='item:1') for i in range(n)]
    def carrier(self,**kw):
        c=dict(code='QA',inner_l=3000,inner_w=2000,inner_h=2000,door_w=2000,door_h=2000,payload=10000,open_top=False);c.update(kw);return c
    def test_no_wall_margin_and_no_hard_cylinder_voids(self):
        bags=self.bags(96);before=copy.deepcopy(bags);p=f.pack(m,bags,self.carrier())
        self.assertEqual(p['count'],1);b=p['containers'][0];a=b['flexible_fill'][0]
        self.assertEqual(a['quantity'],96);self.assertEqual(a['volume_m3'],12);self.assertEqual(b['volume_utilization'],100)
        self.assertEqual(b['total_weight'],672);self.assertEqual(b['placements'],[]);self.assertEqual(bags,before)
        self.assertEqual(b['flexible_zones'][0]['x'],0);self.assertEqual(b['flexible_zones'][0]['h'],2000)
    def test_quantity_weight_volume_conserved_across_added_containers(self):
        p=f.pack(m,self.bags(100),self.carrier());alloc=[a for b in p['containers'] for a in b.get('flexible_fill',[])]
        self.assertEqual(p['count'],2);self.assertEqual(sum(a['quantity'] for a in alloc),100);self.assertEqual(sum(a['weight_kg'] for a in alloc),700);self.assertEqual(sum(a['volume_m3'] for a in alloc),12.5)
        self.assertEqual(len({name for a in alloc for name in a['names']}),100)
    def test_payload_is_not_bypassed_by_volume_mode(self):
        p=f.pack(m,self.bags(100),self.carrier(payload=70));self.assertEqual(p['count'],10)
        self.assertTrue(all(b['total_weight']<=70 for b in p['containers']))
    def test_void_subtraction_conserves_union_and_has_no_equipment_overlap(self):
        b=dict(inner_l=3000,inner_w=2000,inner_h=2000,placements=[dict(x=500,y=500,z=0,l=1000,w=1000,h=1000)])
        free=f.voids(b);self.assertAlmostEqual(sum(p[3]*p[4]*p[5] for p in free),11e9)
        for p in free:
            self.assertTrue(p[0]+p[3]<=500 or p[0]>=1500 or p[1]+p[4]<=500 or p[1]>=1500 or p[2]>=1000)
    def test_volume_only_never_passes_formal_or_exports_fake_coordinates(self):
        p=f.pack(m,self.bags(100),self.carrier());self.assertTrue(m._formal_export_issues(p,p['code']))
        self.assertTrue(all(b['engineering_audit']['status']=='需复核' for b in p['containers']))
        rows=m._container_load_records(p,0,[],[]);self.assertEqual(sum(r['qty'] for r in rows),100);self.assertEqual(sum(r['weight'] for r in rows),700)
        self.assertTrue(all(r['x']==r['y']==r['z']=='' for r in rows))
    def test_volume_allocation_changes_manual_signature(self):
        p=f.pack(m,self.bags(100),self.carrier());b=p['containers'][0];old=m._container_box_signature(b);b['flexible_fill'][0]['quantity']-=1
        self.assertNotEqual(old,m._container_box_signature(b))
