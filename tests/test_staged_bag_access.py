import unittest,copy
from app.bag_access import plan_access,overlap,as_dict
from app import flexible_fill
from test_v58_regression import IsolatedDbMixin,m

class StagedAccessTests(IsolatedDbMixin,unittest.TestCase):
    def box(self,placements):return dict(inner_l=6000,inner_w=2400,inner_h=2700,door_w=2300,door_h=2500,placements=placements)
    def cargo(self,**kw):
        q=dict(x=1000.,y=1000.,z=0.,l=2000.,w=600.,h=600.,seq=1,type='木箱',weight_kg=100,top_load_verified=False,max_top_load_kg=None);q.update(kw);return q
    def test_front_and_intermediate_pocket_fill_before_future_equipment(self):
        box=self.box([self.cargo(x=1000,y=0,w=2400,l=1000),self.cargo(x=4000,y=0,w=2400,l=1000,seq=2)])
        steps=plan_access(box)
        self.assertTrue(any(s['space'][0]==0 and s['space'][3]==1000 for s in steps))
        pocket=next(s for s in steps if s['space'][0]==2000)
        self.assertIn(2,pocket['future_sequences']);self.assertIn(1,pocket['installed_sequences'])
        for s in steps:
            for q in box['placements']:
                if q['seq'] in s['installed_sequences']:self.assertFalse(overlap(s['corridor'],q))
                else:self.assertGreaterEqual(q['x'],s['frontier_mm']-1e-6)
    def test_thin_side_gap_and_low_door_remain_excluded(self):
        b=self.box([self.cargo(x=0,y=100,l=6000,w=1900,h=2200)])
        self.assertEqual(plan_access(b),[])
        b=self.box([]);b['door_h']=1700;self.assertEqual(plan_access(b),[])
    def test_verified_top_requires_budget_flat_support_and_reachable_standing_area(self):
        q=self.cargo(max_top_load_kg=70,top_load_verified=True,top_load_source_type='MANUFACTURER_DATA')
        s=next(s for s in plan_access(self.box([q])) if s['kind']=='verified_crate_top')
        self.assertEqual(s['top_budget_kg'],70);self.assertEqual(s['support_sequence'],1)
        self.assertFalse(overlap(s['work_area'],q))
        for change in [dict(top_load_verified=False),dict(top_load_source_type='UNCONFIRMED'),dict(type='铁桶'),dict(h=2000),dict(z=600)]:
            altered=dict(q,**change)
            self.assertFalse(any(s['kind']=='verified_crate_top' for s in plan_access(self.box([altered]))))
    def test_existing_overhead_mass_reduces_top_budget_and_blocks_covered_surface(self):
        q=self.cargo(max_top_load_kg=70,top_load_verified=True,top_load_source_type='ENGINEERING_CALC')
        upper=self.cargo(x=2500,y=1000,z=600,l=500,w=600,h=100,seq=2,weight_kg=80,type='裸装')
        self.assertFalse(any(s['kind']=='verified_crate_top' for s in plan_access(self.box([q,upper]))))
    def test_filled_areas_cannot_block_later_work_corridors(self):
        b=self.box([self.cargo(x=1000,y=0,w=1000),self.cargo(x=4000,y=1000,w=1000,l=1000,seq=2)])
        prior=[]
        for s in plan_access(b):
            for old in prior:self.assertFalse(overlap(as_dict(old['space']),s['corridor']))
            prior.append(s)
    def test_real_pack_counts_support_budget_and_splits_whole_bags_only(self):
        c=dict(code='QA',inner_l=6000,inner_w=2400,inner_h=2700,door_w=2300,door_h=2500,payload=20000,open_top=False)
        normal=[dict(name='crate',length_mm=2000,width_mm=600,height_mm=600,weight_kg=100,type='木箱',rotatable=False,stackable=False,max_top_load_kg=14,top_load_verified=True,top_load_source_type='MANUFACTURER_DATA')]
        bags=[dict(name='bag-'+str(i),length_mm=560,width_mm=560,height_mm=800,weight_kg=7,type='编织袋',flexible_unit_volume_m3=.125) for i in range(400)]
        p=flexible_fill.pack(m,normal+bags,c)
        self.assertEqual(sum(a['quantity'] for b in p['containers'] for a in b.get('flexible_fill',[])),400)
        for b in p['containers']:
            for s in b.get('bag_access_audit',{}).get('loading_steps',[]):
                self.assertAlmostEqual(s['quantity']*.125,s['volume_m3'])
                if s['top_budget_kg'] is not None:self.assertLessEqual(s['weight_kg'],s['top_budget_kg'])
    def test_straight_corridor_must_pass_inside_centered_door_opening(self):
        b=self.box([self.cargo(x=0,y=800,l=6000,w=1600)])
        b['door_w']=2000
        self.assertEqual(plan_access(b),[])
        empty=self.box([]);empty['door_w']=2000
        for s in plan_access(empty):
            self.assertGreaterEqual(s['corridor']['y'],200)
            self.assertLessEqual(s['corridor']['y']+800,2200)
