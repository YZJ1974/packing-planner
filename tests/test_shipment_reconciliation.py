import copy
import json
import hashlib
import unittest
from pathlib import Path
from test_v58_regression import IsolatedDbMixin, m
from app.shipment_source import record, state, resolve_weights
from fastapi.testclient import TestClient


class ShipmentReconciliationTests(IsolatedDbMixin, unittest.TestCase):
    def headers(self):
        return ['序号\nÍtem', '设备\nEquipo', '规格型号\nModelo / especificación',
                '发货厂家\nFabricante / proveedor', '单位\nUnidad',
                '包装数量\nCant. de embalajes', '尺寸长×宽×高 (mm)\nDimensiones L×An×Al (mm)',
                '总重量 (kg)\nPeso total (kg)', '发货时间', '备注\nObservaciones']

    def parse(self, rows):
        cols = m.identify_columns(self.headers())
        return m.parse_material_rows([self.headers()] + rows, cols, 1, self.headers())

    def test_bilingual_header_does_not_use_manufacturer_or_model(self):
        tables = [{'sheet': '总清单', 'rows': [['公司设备发货计划'],
                  ['合同编号', '', '', '项目名称 / Proyecto:'], self.headers(),
                  [1, '预冷机\nUnidad de preenfriamiento', 'UF-10100/6.2', '厂家',
                   '台\nunidad', 1, '4000*1700*2000mm', 4000]]}]
        h = m.find_best_header(tables)
        self.assertEqual(h['header_idx'], 2)
        self.assertEqual(h['columns']['name'], 1)
        self.assertEqual(h['columns']['size'], 6)
        self.assertEqual(h['columns']['total_weight_kg'], 7)
        self.assertEqual(h['columns']['unit'], 4)

    def test_cubic_metres_and_barrels_have_different_count_semantics(self):
        rows, skipped, meta = self.parse([
            [8, '分子筛/氧化铝\nTamiz', '', '厂家', '桶\ntambor', 41, 'φ600*1000mm,150KG', 6000],
            [13, '珠光砂\nPerlita', '', '厂家', '立方\nm³', 950, 'φ560*800(单包，1立方8包）', 53200]])
        self.assertFalse(skipped)
        self.assertEqual(meta['weight_conflicts'], 1)
        self.assertEqual((rows[0]['qty'], rows[0]['package_type']), (41, '铁桶'))
        self.assertEqual((rows[0]['length_mm'], rows[0]['height_mm']), (600, 1000))
        self.assertEqual((rows[1]['qty'], rows[1]['weight_kg']), (7600, 7))
        self.assertIn('PERLITE_FLEXIBLE_VOLUME', rows[1]['size_note'])

    def test_set_count_and_dimension_punctuation_are_not_lost(self):
        rows, skipped, meta = self.parse([
            [21, '常温调节阀', '', '', '套', 1, '900*500*400，100KG，五件（预估）', 500],
            [22, '常温手动阀', '', '', '套', 1, '1000*1000*1000，400KG，八件（预估）', 4000],
            [41, '液氩泵', '', '', '台', 2, '1100*,600*720，400KG', 800]])
        self.assertFalse(skipped)
        self.assertEqual([r['qty'] for r in rows], [5, 8, 2])
        self.assertEqual(meta['weight_conflicts'], 1)
        self.assertEqual(rows[2]['width_mm'], 600)

    def test_explicit_bulk_transport_never_enters_container(self):
        rows, _, _ = self.parse([[26, '闭式冷却塔', '', '', '台', 1,
                                 '8000*4200*7400mm', 12500, '', '两段散货船']])
        self.assertIn('SHIPMENT_NON_CONTAINER', rows[0]['size_note'])
        self.assertFalse(m.fits_container(dict(transport_excluded=True), {}))

    def test_source_check_detects_extra_old_material_and_numerical_changes(self):
        root = m.DB_PATH.parent
        rows = [dict(name='设备A', qty=2, length_mm=1000, width_mm=800, height_mm=700, weight_kg=50)]
        record(root, 'current.xlsx', b'original', rows, rows, 'replace')
        self.assertFalse(state(root, rows)['issues'])
        changed = copy.deepcopy(rows); changed[0]['weight_kg'] = 51
        self.assertTrue(state(root, changed)['issues'])
        self.assertTrue(state(root, rows + [dict(rows[0], name='旧氧压机')])['issues'])
        record(root, 'extra.xlsx', b'extra', rows, rows, 'append')
        self.assertEqual(state(root, rows)['filename'], 'current.xlsx')

    def test_confirmed_weights_apply_only_to_exact_file_and_exact_row(self):
        root = m.DB_PATH.parent;raw = b'source'
        fixes = dict(sha256=hashlib.sha256(raw).hexdigest(), authorization='用户确认',
                     rows=[dict(row=9, name='分子筛', qty=41, weight_kg=150)])
        (root/'import_resolutions.json').write_text(json.dumps(fixes))
        rows = [dict(source_row=9, name='分子筛', qty=41, weight_kg=6000/41,
                     weight_conflict=True, size_note='原表矛盾')]
        fixed, meta = resolve_weights(root, raw, rows, [('S', {'weight_conflicts': 1})])
        self.assertEqual(fixed[0]['weight_kg'], 150)
        self.assertEqual(meta[0][1]['weight_conflicts'], 0)
        self.assertEqual(resolve_weights(root, b'different', rows, [])[0], rows)
        with self.assertRaises(ValueError):resolve_weights(root, raw, rows + rows, [])

    def test_all_container_exports_block_known_source_mismatch(self):
        (m.DB_PATH.parent/'shipment_source.json').write_text(json.dumps(
            dict(filename='new.xlsx', issues=['旧材料不属于本次清单'])))
        client = TestClient(m.app)
        for route in ('/export-container-layout-word', '/export-container-svg', '/export-container-load-list'):
            response = client.post(route, json=dict(scheme='40HQ', mode='planning', container_index=1))
            self.assertEqual(response.status_code, 409, route)
            self.assertIn('清单来源', response.json()['error'])

    def test_mixed_candidate_accounts_for_every_unit_without_changing_dimensions(self):
        from app.mixed_flexible import candidates
        rows=[]
        for name,qty,l,w,h,kg,note in [('高设备',1,2220,2020,3100,1500,''),
                                     ('低设备',1,1200,700,900,400,''),
                                     ('珠光砂',10,800,560,560,7,'PERLITE_FLEXIBLE_VOLUME；PERLITE_VOID_FILL')]:
            rows.append(dict(name=name,qty=qty,length_mm=l,width_mm=w,height_mm=h,
                             weight_kg=kg,package_type='编织袋' if name=='珠光砂' else '裸装',
                             stackable=0,rotatable=1,size_note=note))
        m.insert_imported_shipping_rows(rows,'replace')
        items,crates=m.fetch_state();packages=m.get_packages(items,crates)
        plans=candidates(m,packages,m._active_containers())
        self.assertTrue(plans)
        for plan in plans:
            located=[q for b in plan['containers'] for q in b['placements']]
            bags=[a for b in plan['containers'] for a in b.get('flexible_fill',[])]
            self.assertEqual(len(located),2)
            self.assertEqual(len(set(q['seq'] for q in located)),2)
            self.assertEqual(sum(a['quantity'] for a in bags),10)
            self.assertAlmostEqual(sum(b['total_weight'] for b in plan['containers']),1970)
            self.assertEqual(sorted(sorted([q['l'],q['w'],q['h']]) for q in located),
                             sorted([[2020.,2220.,3100.],[700.,900.,1200.]]))

    def test_conflicting_upload_preserves_existing_materials(self):
        row=dict(name='原材料',qty=1,length_mm=100,width_mm=100,height_mm=100,
                 weight_kg=10,package_type='裸装',stackable=0,rotatable=1)
        m.insert_imported_shipping_rows([row],'replace');before=m.fetch_state()[0]
        raw='设备,单位,数量,尺寸,总重量\n分子筛,桶,41,φ600*1000mm 150kg,6000\n'.encode('utf-8-sig')
        response=TestClient(m.app).post('/upload-materials',files={'file':('source.csv',raw,'text/csv')},data={'mode':'replace'},follow_redirects=False)
        self.assertIn('error=',response.headers['location'])
        self.assertEqual(m.fetch_state()[0],before)

    def test_unparsed_shipping_row_prevents_partial_replacement(self):
        raw='设备,单位,数量,尺寸,总重量\n设备A,台,1,1000*800*600mm,50\n设备B,台,1,1000*800mm,60\n'.encode('utf-8-sig')
        response=TestClient(m.app).post('/upload-materials',files={'file':('source.csv',raw,'text/csv')},data={'mode':'replace'},follow_redirects=False)
        self.assertIn('error=',response.headers['location'])
        self.assertEqual(m.fetch_state()[0],[])

    def test_successful_replacement_records_exact_file_source(self):
        raw='设备,单位,数量,尺寸,总重量\n设备A,台,2,1000*800*600mm,100\n'.encode('utf-8-sig')
        response=TestClient(m.app).post('/upload-materials',files={'file':('source.csv',raw,'text/csv')},data={'mode':'replace'},follow_redirects=False)
        self.assertNotIn('error=',response.headers['location'])
        items=m.fetch_state()[0];source=m._shipment_source_state(items)
        self.assertEqual(source['status'],'verified')
        self.assertEqual(source['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(source['source_rows'],1)
        self.assertEqual(items[0]['name'],'设备A')

    def test_fractional_cubic_metres_and_missing_quantity_are_not_truncated(self):
        rows,_,_=self.parse([[13,'珠光砂','','','立方',1.5,'φ560*800，1立方8包',84]])
        self.assertEqual(rows[0]['qty'],12)
        with self.assertRaises(ValueError):self.parse([[1,'设备A','','','台',None,'1000*800*600',50]])
        with self.assertRaises(ValueError):self.parse([[1,'设备A','','','台',1.5,'1000*800*600',50]])
