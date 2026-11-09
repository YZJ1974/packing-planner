import unittest,copy,json
from unittest.mock import patch
from test_v58_regression import IsolatedDbMixin,m
from app.bag_top_policy import signature,approved,applicable
from app.bag_top_access import top_stages
from app.bag_access import plan_access,overlap,as_dict
from app import flexible_fill

class ProjectBagTopTests(IsolatedDbMixin,unittest.TestCase):
    def q(self,**kw):
        q=dict(seq=1,asu_key='item:10',name='absorber',type='裸装',x=1000.,y=200.,z=0.,l=2000.,w=1600.,h=1600.,weight_kg=100,display_shape='horizontal_cylinder');q.update(kw);return q
    def box(self,qs):return dict(inner_l=6000.,inner_w=2400.,inner_h=2700.,door_w=2300.,door_h=2500.,payload=20000,placements=qs)
    def policy(self,qs):return dict(schema=1,enabled=True,source='USER_PROJECT_DECLARATION',max_bag_density_kg_m3=56,approved_package_signatures=[signature(q) for q in qs],statement='test project only')
    def test_user_permission_allows_curved_bare_top_without_invented_rating(self):
        q=self.q();b=self.box([q]);before=copy.deepcopy(q)
        self.assertFalse(any(s['kind']=='user_confirmed_top' for s in plan_access(b)))
        steps=plan_access(b,policy=self.policy([q]));top=next(s for s in steps if s['kind']=='user_confirmed_top')
        self.assertIsNone(top['top_budget_kg']);self.assertEqual(top['confirmation_source'],'USER_PROJECT_DECLARATION');self.assertEqual(q,before)
        self.assertFalse(overlap(as_dict(top['space']),q));self.assertGreater(top['required_delivery_reach_mm'],0)
    def test_permission_is_bound_to_identity_dimensions_name_and_mass(self):
        q=self.q();p=self.policy([q]);self.assertTrue(approved(q,p))
        self.assertTrue(approved(dict(q,l=q['w'],w=q['l']),p))
        for change in [dict(name='new equipment'),dict(weight_kg=101),dict(asu_key='item:11'),dict(l=2100)]:self.assertFalse(approved(dict(q,**change),p))
        self.assertEqual(top_stages(self.box([dict(q,name='other')]),p),[])
    def test_heavier_bag_density_does_not_inherit_declaration(self):
        p=self.policy([self.q()]);bag=dict(weight_kg=7,flexible_unit_volume_m3=.125)
        self.assertTrue(applicable(p,[bag]));self.assertEqual(applicable(p,[dict(bag,weight_kg=8)]),{})
    def test_explicit_known_zero_rating_is_not_bypassed(self):
        q=self.q(top_load_verified=True,top_load_source_type='MANUFACTURER_DATA',max_top_load_kg=0)
        self.assertEqual(top_stages(self.box([q]),self.policy([q])),[])
    def test_known_ancestor_limit_remains_effective(self):
        lower=self.q(h=1000,max_top_load_kg=80,top_load_verified=True,top_load_source_type='ENGINEERING_CALC')
        upper=self.q(seq=2,asu_key='item:11',name='bridge',z=1000,h=400,weight_kg=60)
        tops=top_stages(self.box([lower,upper]),self.policy([lower,upper]))
        self.assertTrue(tops);self.assertTrue(all(s['top_budget_kg']==20 for s in tops))
    def test_schedule_spaces_do_not_overlap_and_future_insertions_are_clear(self):
        qs=[self.q(),self.q(seq=2,asu_key='item:11',name='platform',x=3000,l=2000,h=1000)]
        earlier=[]
        for s in plan_access(self.box(qs),policy=self.policy(qs)):
            for old in earlier:
                self.assertFalse(overlap(as_dict(old['space']),as_dict(s['space'])))
                self.assertFalse(overlap(as_dict(old['space']),s['corridor']))
                if s.get('delivery_route'):self.assertFalse(overlap(as_dict(old['space']),s['delivery_route']))
            for q in qs:
                if q['seq'] in s['future_sequences']:self.assertFalse(overlap(as_dict(s['space']),dict(q,l=6000-q['x'])))
            earlier.append(s)
    def test_policy_changes_invalidate_plan_cache(self):
        items,crates=m.fetch_state()
        with patch.object(m,'_bag_fill_policy',return_value={}):a=m._state_fingerprint(items,crates)
        with patch.object(m,'_bag_fill_policy',return_value=self.policy([self.q()])):b=m._state_fingerprint(items,crates)
        self.assertNotEqual(a,b)
    def test_full_pack_preserves_names_mass_and_shared_top_limit(self):
        normal=[dict(name='crate',asu_key='crate:1',length_mm=2000,width_mm=1600,height_mm=1000,weight_kg=100,type='木箱',rotatable=False,stackable=False,max_top_load_kg=28,top_load_verified=True,top_load_source_type='MANUFACTURER_DATA')]
        bags=[dict(name='bag-'+str(i),asu_key='item:2',length_mm=560,width_mm=560,height_mm=800,weight_kg=7,type='编织袋',flexible_unit_volume_m3=.125) for i in range(400)]
        carrier=dict(code='QA',inner_l=6000,inner_w=2400,inner_h=2700,door_w=2300,door_h=2500,payload=20000,open_top=False)
        with patch.object(m,'_bag_fill_policy',return_value=self.policy(normal)):
            p=flexible_fill.pack(m,normal+bags,carrier)
        allocations=[a for b in p['containers'] for a in b.get('flexible_fill',[])]
        self.assertEqual(sum(a['quantity'] for a in allocations),400);self.assertEqual(sum(a['weight_kg'] for a in allocations),2800)
        self.assertEqual(len({n for a in allocations for n in a['names']}),400)
        for b in p['containers']:
            by_support={}
            for step in b.get('bag_access_audit',{}).get('loading_steps',[]):
                if step['zone']['z']>0:by_support[step['support_sequence']]=by_support.get(step['support_sequence'],0)+step['weight_kg']
            self.assertTrue(all(v<=28 for v in by_support.values()))
        self.assertTrue(m._formal_export_issues(p,p['code']))
