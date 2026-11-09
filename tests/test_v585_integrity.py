import unittest, re
from unittest.mock import patch
from test_v58_regression import IsolatedDbMixin,m,VALID_PDF
from fastapi.testclient import TestClient
from app.release_rules import item_evidence_ok

class Integrity(IsolatedDbMixin,unittest.TestCase):
    def setUp(self):
        super().setUp();self.set_user();self.client=TestClient(m.app,raise_server_exceptions=True)
    def seed(self,name='wood',package='木箱',qty=1):
        con=m.db();iid=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,package_type,stackable,rotatable) VALUES(?,?,1000,800,600,120,?,1,1)",(name,qty,package)).lastrowid;con.commit();con.close();return iid
    def rows(self):
        con=m.db();rows=[dict(r) for r in con.execute('SELECT * FROM items ORDER BY id')];con.close();return rows
    def confirm(self,iid):
        for path,data in [('confirm-size',dict(evidence_length_mm=1000,evidence_width_mm=800,evidence_height_mm=600,evidence_scope='item')),('confirm-weight',dict(evidence_weight_kg=120,evidence_scope='item')),('cg',dict(cg_x_mm=700,cg_y_mm=300,cg_z_mm=200,cg_source_type='FIELD_MEASURED',evidence_cg_x_mm=700,evidence_cg_y_mm=300,evidence_cg_z_mm=200))]:
            r=self.client.post(f'/items/{iid}/{path}',data=data,files={'evidence':('record.pdf',VALID_PDF,'application/pdf')},follow_redirects=False)
            self.assertNotIn('error=',r.headers['location'])
    def repack(self):return m.apply_smart_crates(1800,1500,1500,4000,50,45,max_items_per_crate=1)
    def valid(self,row):
        for kind in ('size','weight'):self.assertTrue(item_evidence_ok(row,m._evidence_matches,m._evidence_summary,kind),row)
        self.assertTrue(m._evidence_matches(row['cg_evidence_id'],scope_type='item',scope_key=str(row['id']),entity_types=('item',),entity_ids=(str(row['id']),),evidence_types=('item_cg_record',),require_verified=True))
    def test_bare_record_and_all_evidence_fields_unchanged(self):
        iid=self.seed('bare','裸装');self.confirm(iid);self.seed();m.fetch_state();before=self.rows()[0]
        self.repack();after=self.rows()[0];self.assertEqual(before,after);self.valid(after)
    def test_wood_identity_and_confirmations_survive_repack(self):
        iid=self.seed();self.confirm(iid);m.fetch_state();before=self.rows()[0];self.repack();after=self.rows()[0]
        self.assertEqual({k:v for k,v in before.items() if k!='crate_id'},{k:v for k,v in after.items() if k!='crate_id'});self.valid(after)
    def test_split_evidence_and_repeated_repacking_preserve_quantity_and_claims(self):
        iid=self.seed(qty=3);self.confirm(iid)
        for _ in range(2):
            self.repack();rows=self.rows();self.assertEqual(sum(r['qty'] for r in rows),3)
            self.assertTrue(any(r['id']==iid for r in rows));self.assertEqual(sum(r['qty']*r['weight_kg'] for r in rows),360)
            for r in rows:self.valid(r)
    def test_split_lineage_cannot_approve_changed_weight_or_unrelated_item(self):
        iid=self.seed(qty=2);self.confirm(iid);self.repack();child=next(r for r in self.rows() if r['id']!=iid)
        altered=dict(child,weight_kg=900)
        self.assertFalse(item_evidence_ok(altered,m._evidence_matches,m._evidence_summary,'weight'))
        other=self.seed('unrelated','裸装');altered=dict(child,id=other)
        self.assertFalse(item_evidence_ok(altered,m._evidence_matches,m._evidence_summary,'size'))
    def test_concurrent_insert_stops_commit_and_retains_both_records(self):
        self.seed();original=m._pack_wood_items_to_crates
        def race(*a,**kw):
            result=original(*a,**kw);self.seed('concurrent','裸装');return result
        with patch.object(m,'_pack_wood_items_to_crates',side_effect=race):
            with self.assertRaisesRegex(ValueError,'发生变化'):self.repack()
        self.assertEqual(len(self.rows()),2);self.assertFalse(m.fetch_state()[1])
    def test_concurrent_edit_stops_commit_without_overwrite(self):
        iid=self.seed();original=m._pack_wood_items_to_crates
        def race(*a,**kw):
            result=original(*a,**kw);con=m.db();con.execute('UPDATE items SET weight_kg=321 WHERE id=?',(iid,));con.commit();con.close();return result
        with patch.object(m,'_pack_wood_items_to_crates',side_effect=race):
            with self.assertRaisesRegex(ValueError,'发生变化'):self.repack()
        self.assertEqual(self.rows()[0]['weight_kg'],321)
    def test_partial_solver_result_does_not_remove_units(self):
        self.seed();m.fetch_state();before=self.rows()
        with patch.object(m,'_pack_wood_items_to_crates',return_value=([],0)):
            with self.assertRaisesRegex(ValueError,'件数'):self.repack()
        self.assertEqual(self.rows(),before)
    def test_failed_write_rolls_back_existing_crates_and_items(self):
        self.seed();self.repack();m.fetch_state();con=m.db();before=m._reboxing_snapshot(con);con.close();original=m._pack_wood_items_to_crates
        def corrupt(*a,**kw):
            result=original(*a,**kw);result[0][0]['length_mm']=None;return result
        with patch.object(m,'_pack_wood_items_to_crates',side_effect=corrupt):
            with self.assertRaises(Exception):self.repack()
        con=m.db();after=m._reboxing_snapshot(con);con.close();self.assertEqual(after,before)
    def test_zero_gross_mapped_import_rejected_before_any_replace(self):
        self.seed('keep','裸装')
        csv='名称,数量,长度mm,宽度mm,高度mm,重量kg,包装方式\n零毛重木箱,1,1000,800,600,0,木箱\n'.encode('utf-8-sig')
        r=self.client.post('/upload-materials',files={'file':('zero.csv',csv,'text/csv')},data={'mode':'replace'},follow_redirects=False)
        token=re.search(r'name="token"[^>]*value="([^"]+)"',r.text).group(1)
        r=self.client.post('/confirm-import',data={'token':token,'data_start':2,'name_col':0,'qty_col':1,'length_col':2,'width_col':3,'height_col':4,'weight_col':5,'package_col':6},follow_redirects=False)
        self.assertIn('error=',r.headers['location']);self.assertEqual(self.rows()[0]['name'],'keep');self.assertEqual(self.client.get('/').status_code,200)
    def test_legacy_zero_gross_can_be_repaired_through_existing_evidence_gate(self):
        row=dict(name='invalid',qty=1,length_mm=1000,width_mm=800,height_mm=600,weight_kg=120,package_type='木箱',stackable=1,rotatable=1)
        m.insert_imported_shipping_rows([row]);con=m.db();cid=con.execute('SELECT id FROM crates').fetchone()[0];con.execute('UPDATE crates SET actual_gross_weight_kg=0');con.commit();con.close()
        r=self.client.get('/');self.assertEqual(r.status_code,200);self.assertIn('请先修正已包装单元毛重',r.text)
        r=self.client.post(f'/crates/{cid}/actual-gross',data={'actual_gross_weight_kg':120,'evidence_weight_kg':120},follow_redirects=False);self.assertIn('error=',r.headers['location'])
        r=self.client.post(f'/crates/{cid}/actual-gross',data={'actual_gross_weight_kg':120,'evidence_weight_kg':120},files={'evidence':('ticket.pdf',VALID_PDF,'application/pdf')},follow_redirects=False)
        self.assertNotIn('error=',r.headers['location']);r=self.client.get('/');self.assertEqual(r.status_code,200);self.assertNotIn('请先修正已包装单元毛重',r.text)
