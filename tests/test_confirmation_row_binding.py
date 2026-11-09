import copy,hashlib,json,unittest
from test_v58_regression import IsolatedDbMixin,m
from app.shipment_source import resolve_weights,source_row_digest

class ConfirmedRowBindingTests(IsolatedDbMixin,unittest.TestCase):
 def setUp(self):
  super().setUp()
  self.row=dict(source_row=9,name='分子筛/氧化铝',qty=41,length_mm=600,width_mm=600,height_mm=1000,weight_kg=6000/41,weight_conflict=True,size_note='原表41桶150kg，总重6000kg',package_type='铁桶')
  self.res=dict(sha256=hashlib.sha256(b'original').hexdigest(),authorization='用户确认150kg',rows=[dict(row=9,name=self.row['name'],qty=41,weight_kg=150,source_row_sha256=source_row_digest(self.row))])
  self.save()
 def save(self): (m.DB_PATH.parent/'import_resolutions.json').write_text(json.dumps(self.res))
 def resolve(self,rows=None):return resolve_weights(m.DB_PATH.parent,b'revised workbook',rows or [self.row],[('S',dict(weight_conflicts=1))])
 def test_revised_workbook_retains_unchanged_confirmed_row(self):
  before=copy.deepcopy(self.row);rows,meta=self.resolve();self.assertEqual(rows[0]['weight_kg'],150);self.assertEqual(meta[0][1]['weight_conflicts'],0);self.assertEqual(self.row,before)
 def test_any_changed_source_fact_keeps_conflict(self):
  for field,value in [('qty',42),('length_mm',610),('weight_kg',140),('size_note','新称重数据'),('package_type','木箱')]:
   row=dict(self.row,**{field:value});rows,meta=self.resolve([row]);self.assertEqual(rows,[row]);self.assertEqual(meta[0][1]['weight_conflicts'],1)
 def test_ambiguous_duplicate_is_never_resolved(self):
  rows,meta=self.resolve([self.row,self.row]);self.assertTrue(all(r['weight_conflict'] for r in rows));self.assertEqual(meta[0][1]['weight_conflicts'],2)
 def test_legacy_confirmation_never_spills_to_other_files(self):
  del self.res['rows'][0]['source_row_sha256'];self.save();rows,meta=self.resolve();self.assertEqual(rows,[self.row]);self.assertEqual(meta[0][1]['weight_conflicts'],1)
 def test_filter_pose_retained_only_for_unchanged_original_row(self):
  row=dict(source_row=4,name='过滤器',qty=1,length_mm=2220,width_mm=2020,height_mm=3100,size_note='原表',weight_kg=1500)
  self.res['rows']=[];self.res['handling_confirmations']=[dict(row=4,name='过滤器',qty=1,source_row_sha256=source_row_digest(row),source_dimensions_mm=[2220,2020,3100],planning_dimensions_mm=[3100,2020,2220],authorization='已确认放倒')];self.save()
  rows,_=self.resolve([row]);self.assertEqual([rows[0][k] for k in ('length_mm','width_mm','height_mm')],[3100,2020,2220])
  changed=dict(row,weight_kg=1600);rows,_=self.resolve([changed]);self.assertEqual(rows,[changed])
