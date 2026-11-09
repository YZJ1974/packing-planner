import unittest,copy
from test_v58_regression import m,IsolatedDbMixin
from app import bulk_fill

class BulkFillTests(IsolatedDbMixin,unittest.TestCase):
    def bags(self,n=256,**kw):
        p=dict(name='bag',length_mm=500,width_mm=500,height_mm=800,weight_kg=7,type='编织袋',stackable=False,rotatable=True,void_fill=True)
        p.update(kw);return [dict(p,name='bag-'+str(i)) for i in range(n)]
    def carrier(self):return dict(code='QA',inner_l=3200,inner_w=2200,inner_h=2400,door_w=2200,door_h=2400,payload=10000,open_top=False)
    def test_batch_identity_keeps_changed_mass_separate(self):
        bags=self.bags();bags[-1]['weight_kg']=8;normal,groups=bulk_fill.split(bags)
        self.assertFalse(groups);self.assertEqual(len(normal),256)
    def test_fill_priority_follows_equipment(self):
        bag=self.bags(1)[0];other=dict(bag,name='equipment',void_fill=False,weight_kg=1)
        self.assertEqual(m._ordered_packages([bag,other])[0]['name'],'equipment')
    def test_replicated_layout_conserves_all_bags_and_constraints(self):
        bags=self.bags();before=copy.deepcopy(bags);plan=bulk_fill.pack(m,bags,self.carrier())
        placements=[q for b in plan['containers'] for q in b['placements']]
        self.assertEqual(len(placements),256);self.assertEqual(len({q['name'] for q in placements}),256)
        self.assertEqual(sum(q['weight_kg'] for q in placements),1792);self.assertEqual(bags,before)
        self.assertTrue(plan['void_fill_audit']['identical_layout_copies']>0)
        self.assertTrue(all(not b['engineering_audit']['hard_fail_notes'] for b in plan['containers']))
        self.assertTrue(all(q['z']==0 for q in placements))
    def test_existing_floor_voids_used_before_extra_bins(self):
        item=dict(name='equipment',length_mm=1000,width_mm=500,height_mm=800,weight_kg=100,type='裸装',stackable=False,rotatable=False)
        plan=bulk_fill.pack(m,[item]+self.bags(),self.carrier())
        self.assertGreater(plan['void_fill_audit']['bags_in_existing_voids'],0)
        self.assertTrue(any(q['name']=='equipment' for q in plan['containers'][0]['placements']))
