import csv,io,copy,hashlib,json,unittest
from fastapi.testclient import TestClient
from test_v58_regression import IsolatedDbMixin,m
from app.shipment_source import fingerprint

class MappedShippingConsistencyTests(IsolatedDbMixin,unittest.TestCase):
 def setUp(self):
  super().setUp();self.client=TestClient(m.app)
  self.headers=['序号\nÍtem','设备\nEquipo','规格型号\nModelo / especificación','发货厂家\nFabricante / proveedor','单位\nUnidad','包装数量\nCant. de embalajes','尺寸长×宽×高 (mm)\nDimensiones L×An×Al (mm)','总重量 (kg)\nPeso total (kg)','发货时间','备注\nObservaciones']
  self.rows=[[1,'分子筛/氧化铝\nTamiz','','厂家','桶\ntambor',41,'φ600*1000mm,150KG',6150,'',''],[2,'珠光砂\nPerlita','','厂家','立方\nm³',300,'φ560*800(单包，1立方8包）',16800,'',''],[3,'常温手动阀','','厂家','套',1,'1000*1000*1000，500KG，八件',4000,'',''],[4,'自洁式空气过滤器','','厂家','台',1,'2220*2020*3100',1500,'',''],[5,'闭式冷却塔','','厂家','台',1,'8000*4200*7400',12500,'','两段散货船']]
 def raw(self,rows=None):
  out=io.StringIO();w=csv.writer(out);w.writerow(self.headers);w.writerows(self.rows if rows is None else rows);return out.getvalue().encode('utf-8-sig')
 def session(self,raw):
  tables=m.read_uploaded_tables('shipment.csv',raw);best=m.find_best_header(tables);table=tables[best['table_index']];token=m.save_import_session(table,best,'replace','shipment.csv',raw)
  data=dict(token=token,data_start=best.get('data_start',best['header_idx']+1)+1)
  for k,v in {'name':'name_col','qty':'qty_col','size':'size_col','total_weight_kg':'total_weight_col'}.items():data[v]=best['columns'][k]
  return data,table,best
 def seed(self):
  m.insert_imported_shipping_rows([dict(name='保留货物',qty=1,length_mm=100,width_mm=100,height_mm=100,weight_kg=10,package_type='裸装',stackable=0,rotatable=1)],'replace')
 def facts(self):
  items,crates=m.fetch_state();return [{k:i.get(k) for k in ('name','qty','length_mm','width_mm','height_mm','weight_kg','package_type','size_note')} for i in items],crates
 def test_auto_and_manual_preserve_volume_drums_sets_bilingual_names_and_exclusion(self):
  raw=self.raw();r=self.client.post('/upload-materials',files={'file':('shipment.csv',raw)},data={'mode':'replace'},follow_redirects=False);self.assertNotIn('error=',r.headers['location']);expected=self.facts()
  form,_,_=self.session(raw);r=self.client.post('/confirm-import',data=form,follow_redirects=False);self.assertNotIn('error=',r.headers['location']);self.assertEqual(self.facts(),expected)
  by={i['name']:i for i in self.facts()[0]};self.assertEqual(by['珠光砂']['qty'],2400);self.assertEqual(by['珠光砂']['package_type'],'编织袋');self.assertEqual(by['分子筛/氧化铝']['height_mm'],1000);self.assertEqual(by['分子筛/氧化铝']['package_type'],'铁桶');self.assertEqual(by['常温手动阀']['qty'],8);self.assertIn('SHIPMENT_NON_CONTAINER',by['闭式冷却塔']['size_note'])
 def test_unresolved_weight_conflict_does_not_replace_any_material(self):
  self.seed();before=self.facts();rows=copy.deepcopy(self.rows);rows[0][7]=6000;form,_,_=self.session(self.raw(rows));r=self.client.post('/confirm-import',data=form,follow_redirects=False);self.assertIn('error=',r.headers['location']);self.assertEqual(self.facts(),before)
 def test_partial_shipping_geometry_does_not_replace_any_material(self):
  self.seed();before=self.facts();rows=copy.deepcopy(self.rows);rows[-1][6]='待设计';form,_,_=self.session(self.raw(rows));r=self.client.post('/confirm-import',data=form,follow_redirects=False);self.assertIn('error=',r.headers['location']);self.assertEqual(self.facts(),before)
 def test_confirmed_weight_and_pose_apply_through_manual_mapping(self):
  rows=copy.deepcopy(self.rows);rows[0][7]=6000;raw=self.raw(rows);form,table,best=self.session(raw);parsed,_,_=m.parse_material_rows(table['rows'],best['columns'],best.get('data_start',best['header_idx']+1),best['headers']);sieve=next(r for r in parsed if r['name']=='分子筛/氧化铝');f=next(r for r in parsed if r['name']=='自洁式空气过滤器')
  res=dict(sha256=hashlib.sha256(raw).hexdigest(),authorization='用户确认',rows=[dict(row=sieve['source_row'],name=sieve['name'],qty=41,weight_kg=150)],handling_confirmations=[dict(row=f['source_row'],name=f['name'],qty=1,source_dimensions_mm=[2220,2020,3100],planning_dimensions_mm=[3100,2020,2220],authorization='用户允许放倒')]);(m.DB_PATH.parent/'import_resolutions.json').write_text(json.dumps(res))
  r=self.client.post('/confirm-import',data=form,follow_redirects=False);self.assertNotIn('error=',r.headers['location']);by={i['name']:i for i in m.fetch_state()[0]};self.assertEqual(by['分子筛/氧化铝']['weight_kg'],150);self.assertEqual([by[f['name']][k] for k in ('length_mm','width_mm','height_mm')],[3100,2020,2220])
 def test_stale_mapping_cannot_overwrite_later_material_edit(self):
  self.seed();form,_,_=self.session(self.raw());c=m.db();c.execute('UPDATE items SET weight_kg=20');c.commit();c.close();before=self.facts();r=self.client.post('/confirm-import',data=form,follow_redirects=False);self.assertIn('error=',r.headers['location']);self.assertEqual(self.facts(),before)
 def test_mapped_replace_carries_valid_bag_top_declaration(self):
  raw=self.raw();self.client.post('/upload-materials',files={'file':('shipment.csv',raw)},data={'mode':'replace'},follow_redirects=False);items,_=m.fetch_state();source=m._shipment_source_state(items);policy=dict(schema=2,enabled=True,source='USER_PROJECT_DECLARATION',shipment_sha256=source['sha256'],shipment_fingerprint=source['fingerprint'],approved_package_signatures=[],max_bag_density_kg_m3=56);p=m.DB_PATH.parent/'bag_top_policy.json';p.write_text(json.dumps(policy));form,_,_=self.session(raw);r=self.client.post('/confirm-import',data=form,follow_redirects=False);self.assertNotIn('error=',r.headers['location']);self.assertEqual(json.loads(p.read_text())['shipment_fingerprint'],fingerprint(m.fetch_state()[0]));self.assertTrue(m._bag_fill_policy())
