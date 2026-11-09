import copy,json,tempfile,hashlib
from pathlib import Path
from unittest import TestCase
from app.shipment_source import resolve_weights,record

class AuthorizedTransportPoseTests(TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name);self.raw=b'exact shipment'
        self.row=dict(name='自洁式空气过滤器',qty=1,length_mm=2220,width_mm=2020,height_mm=3100,weight_kg=1500,source_row=4,size_note='原表')
        self.fix=dict(row=4,name=self.row['name'],qty=1,source_dimensions_mm=[2220,2020,3100],planning_dimensions_mm=[3100,2020,2220],authorization='用户允许放倒')
        self.res=dict(sha256=hashlib.sha256(self.raw).hexdigest(),handling_confirmations=[self.fix]);self.save()
    def tearDown(self):self.tmp.cleanup()
    def save(self): (self.data/'import_resolutions.json').write_text(json.dumps(self.res))
    def test_exact_source_applies_only_permutation_and_keeps_weight_quantity(self):
        before=copy.deepcopy(self.row);rows,_=resolve_weights(self.data,self.raw,[self.row],[]);q=rows[0]
        self.assertEqual([q[k] for k in ('length_mm','width_mm','height_mm')],[3100,2020,2220]);self.assertEqual(q['weight_kg'],1500);self.assertEqual(q['qty'],1);self.assertEqual(before,self.row);self.assertIn('原表外形尺寸 2220×2020×3100',q['size_note'])
    def test_another_source_does_not_inherit_pose(self):
        rows,_=resolve_weights(self.data,b'other shipment',[self.row],[]);self.assertEqual(rows,[self.row])
    def test_dimension_shrink_or_unmatched_row_is_blocked(self):
        for change in [dict(planning_dimensions_mm=[3000,2020,2220]),dict(source_dimensions_mm=[2221,2020,3100]),dict(qty=2),dict(name='其他设备')]:
            self.res['handling_confirmations']=[dict(self.fix,**change)];self.save()
            with self.assertRaises(ValueError):resolve_weights(self.data,self.raw,[self.row],[])
    def test_record_retains_user_pose_confirmation(self):
        rows,_=resolve_weights(self.data,self.raw,[self.row],[]);record(self.data,'source.xlsx',self.raw,rows,rows,'replace');s=json.loads((self.data/'shipment_source.json').read_text());self.assertEqual(s['handling_confirmations'],[self.fix])
