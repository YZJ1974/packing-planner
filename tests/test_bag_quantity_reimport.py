import copy,json,unittest
from test_v58_regression import IsolatedDbMixin,m
from app.shipment_source import fingerprint
from app.bag_top_policy import rebind_unchanged_equipment
class BagQuantityReimportTests(IsolatedDbMixin,unittest.TestCase):
 def setUp(self):
  super().setUp();self.old=[dict(name='吸附器',qty=2,length_mm=5320,width_mm=2000,height_mm=2000,weight_kg=3133,package_type='裸装'),dict(name='珠光砂',qty=7600,length_mm=800,width_mm=560,height_mm=560,weight_kg=7,package_type='编织袋',size_note='PERLITE_FLEXIBLE_VOLUME')];self.source=dict(status='verified',sha256='old',fingerprint=fingerprint(self.old));self.new=copy.deepcopy(self.old);self.new[1]['qty']=2400
  self.policy=dict(schema=2,enabled=True,source='USER_PROJECT_DECLARATION',shipment_sha256='old',shipment_fingerprint=self.source['fingerprint']);self.root=m.DB_PATH.parent
 def run_case(self):
  (self.root/'bag_top_policy.json').write_text(json.dumps(self.policy));(self.root/'shipment_source.json').write_text(json.dumps(dict(status='verified',sha256='new',fingerprint=fingerprint(self.new))));return rebind_unchanged_equipment(self.root,self.source,self.old,self.new)
 def test_only_bag_quantity_change_retains_project_declaration(self):
  self.assertTrue(self.run_case());p=json.loads((self.root/'bag_top_policy.json').read_text());self.assertEqual(p['shipment_sha256'],'new');self.assertEqual(p['shipment_fingerprint'],fingerprint(self.new))
 def test_physical_or_density_changes_never_reuse_declaration(self):
  for idx,key,val in [(0,'qty',3),(0,'height_mm',2100),(0,'weight_kg',4000),(0,'package_type','木箱'),(1,'weight_kg',8),(1,'name','新砂袋')]:
   self.new=copy.deepcopy(self.old);self.new[idx][key]=val;self.assertFalse(self.run_case(),(key,val));self.assertEqual(json.loads((self.root/'bag_top_policy.json').read_text()),self.policy)
 def test_stale_old_source_cannot_be_revalidated(self):
  self.source['fingerprint']='stale';self.assertFalse(self.run_case())
