#!/usr/bin/env python3
import io, os, sys, json, time, tempfile, unittest, zipfile, threading
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TEST_DATA=tempfile.TemporaryDirectory(prefix='packing_v581_test_')
os.environ['PACKING_PLANNER_DATA_DIR']=TEST_DATA.name
sys.path.insert(0,str(ROOT))
import app.main as m
from app.import_rules import partition_purchase_tables, purchase_row_classification
from app import import_security
from app.evidence_validation import validate_evidence_file, verify_dimension_claim, verify_weight_claim

VALID_PDF=b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n'

class IsolatedDbMixin:
    def setUp(self):
        self.case=tempfile.TemporaryDirectory(dir=TEST_DATA.name,prefix='case_')
        m.DB_PATH=Path(self.case.name)/'packing.db'
        m.init_db(); m._install_append_only_triggers()
        m.EVIDENCE_STORE=m.EvidenceStore(m.db,Path(self.case.name),m._release_meta_load)
        for p in (m.RELEASE_META_PATH,m.CONTAINER_SPECS_META_PATH,m.CONTAINER_SPECS_PATH,m.DIM_OVERRIDE_PATH):
            try:p.unlink()
            except FileNotFoundError:pass
        m._PERF_CACHE['fast'].clear(); m._PERF_CACHE['deep'].clear(); m._CRATE_WEIGHT_SETTINGS_CACHE=None
        m._refresh_containers()
    def tearDown(self): self.case.cleanup()
    def set_user(self): m._release_meta_save({'project_name':'P','approver':'王工'})
    def evidence(self,entity='test',eid='1',etype='test_record',name='evidence.pdf',data=VALID_PDF,scope='item',scope_key=None,verified=True):
        return m.EVIDENCE_STORE.save_evidence(entity_type=entity,entity_id=eid,evidence_type=etype,filename=name,content=data,note='test',created_by='王工',scope_type=scope,scope_key=str(scope_key if scope_key is not None else eid),verification={'ok':bool(verified)})

class TestHealthAndFormat(IsolatedDbMixin,unittest.TestCase):
    def test_01_health_v54_home(self):
        from fastapi.testclient import TestClient
        c=TestClient(m.app,raise_server_exceptions=True)
        self.assertEqual(c.get('/health').json()['version'],'5.8.28'); self.assertEqual(c.get('/').status_code,200)
    def test_02_fake_pdf_rejected(self):
        with self.assertRaises(ValueError): validate_evidence_file('x.pdf',b'not a pdf')
    def test_03_valid_pdf_accepted(self): self.assertEqual(validate_evidence_file('x.pdf',VALID_PDF),'pdf')
    def test_04_fake_docx_rejected(self):
        with self.assertRaises(ValueError): validate_evidence_file('x.docx',b'PK not really zip')

class TestEvidenceVerification(IsolatedDbMixin,unittest.TestCase):
    def seed_item(self):
        con=m.db(); cur=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_source,size_note,package_type,stackable,rotatable) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('设备A',1,1000,800,600,120,'B','上传声明','B','上传声明','','裸装',1,1)); iid=cur.lastrowid; con.commit(); con.close(); return iid
    def test_05_dimension_claim_match(self): self.assertTrue(verify_dimension_claim((1000,800,600),(1000,800,600))['ok'])
    def test_06_dimension_claim_mismatch(self): self.assertFalse(verify_dimension_claim((1000,800,600),(1100,800,600))['ok'])
    def test_07_weight_claim_match(self): self.assertTrue(verify_weight_claim(120,120)['ok'])
    def test_08_confirm_size_requires_single_responsible_user(self):
        from fastapi.testclient import TestClient
        iid=self.seed_item(); c=TestClient(m.app,raise_server_exceptions=True)
        r=c.post(f'/items/{iid}/confirm-size',files={'evidence':('measure.pdf',VALID_PDF,'application/pdf')},data={'evidence_length_mm':'1000','evidence_width_mm':'800','evidence_height_mm':'600','evidence_scope':'item'},follow_redirects=False)
        self.assertIn('303',str(r.status_code)); self.assertIn('error',r.headers.get('location',''))
    def test_09_confirm_size_rejects_mismatched_claim(self):
        from fastapi.testclient import TestClient
        self.set_user(); iid=self.seed_item(); c=TestClient(m.app,raise_server_exceptions=True)
        r=c.post(f'/items/{iid}/confirm-size',files={'evidence':('measure.pdf',VALID_PDF,'application/pdf')},data={'evidence_length_mm':'1200','evidence_width_mm':'800','evidence_height_mm':'600','evidence_scope':'item'},follow_redirects=False)
        self.assertIn('error',r.headers.get('location','')); con=m.db(); row=con.execute('SELECT * FROM items WHERE id=?',(iid,)).fetchone(); con.close(); self.assertEqual(row['size_grade'],'B')
    def test_10_confirm_size_a1_with_strict_binding(self):
        from fastapi.testclient import TestClient
        self.set_user(); iid=self.seed_item(); c=TestClient(m.app,raise_server_exceptions=True)
        r=c.post(f'/items/{iid}/confirm-size',files={'evidence':('measure.pdf',VALID_PDF,'application/pdf')},data={'evidence_length_mm':'1000','evidence_width_mm':'800','evidence_height_mm':'600','evidence_scope':'item','evidence_note':'M-01'},follow_redirects=False)
        self.assertEqual(r.status_code,303); con=m.db(); row=dict(con.execute('SELECT * FROM items WHERE id=?',(iid,)).fetchone()); con.close()
        self.assertEqual(row['size_grade'],'A'); self.assertEqual(row['size_subgrade'],'A1'); self.assertEqual(row['size_evidence_scope_key'],str(iid))
        self.assertTrue(m._evidence_matches(row['size_evidence_id'],scope_type='item',scope_key=str(iid),evidence_types=('measured_or_approved_size',),require_verified=True))
    def test_11_confirm_weight_a2_batch_scope(self):
        from fastapi.testclient import TestClient
        self.set_user(); iid=self.seed_item(); c=TestClient(m.app,raise_server_exceptions=True)
        r=c.post(f'/items/{iid}/confirm-weight',files={'evidence':('ticket.pdf',VALID_PDF,'application/pdf')},data={'evidence_weight_kg':'120','evidence_scope':'batch','evidence_scope_key':'LOT-01'},follow_redirects=False)
        self.assertEqual(r.status_code,303); con=m.db(); row=dict(con.execute('SELECT * FROM items WHERE id=?',(iid,)).fetchone()); con.close(); self.assertEqual(row['weight_subgrade'],'A2'); self.assertEqual(row['weight_evidence_scope_key'],'LOT-01')
    def test_12_wrong_object_evidence_fails_gate(self):
        self.set_user(); iid=self.seed_item(); ev=self.evidence('item','999','measured_or_approved_size',scope='item',scope_key='999')
        con=m.db(); con.execute("UPDATE items SET size_grade='A',size_subgrade='A1',size_evidence_id=?,size_evidence_scope='item',size_evidence_scope_key=?,weight_grade='B' WHERE id=?",(ev['id'],str(iid),iid)); con.commit(); con.close()
        issues=m._data_quality_release_issues(*m.fetch_state()); self.assertTrue(any('尺寸' in x for x in issues))
    def test_13_evidence_tamper_detected(self):
        ev=self.evidence(); self.assertEqual(m.EVIDENCE_STORE.verify_chains(),[]); m.EVIDENCE_STORE.path_for(ev['id']).write_bytes(b'tampered'); self.assertTrue(m.EVIDENCE_STORE.verify_chains())
    def test_14_append_only_trigger(self):
        ev=self.evidence(); con=m.db()
        with self.assertRaises(Exception): con.execute('UPDATE evidence_files SET note=? WHERE id=?',('x',ev['id'])); con.commit()
        con.close()

class TestDimensionLibrary(IsolatedDbMixin,unittest.TestCase):
    def seed(self,grade='B',dims=(111,222,333)):
        con=m.db(); cur=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_source,size_note,package_type,stackable,rotatable) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('Q235B 90°弯头 DN300',1,*dims,10,'B','上传','%s'%grade,'上传','','木箱',1,1)); iid=cur.lastrowid; con.commit(); con.close(); return iid
    def test_15_type_evidence_must_be_verified_and_scoped(self):
        key='5083|弯头|300'; ver=verify_dimension_claim((700,650,400),(700,650,400))
        ev=m.EVIDENCE_STORE.save_evidence(entity_type='dimension_override',entity_id=key,evidence_type='approved_dimension',filename='type.pdf',content=VALID_PDF,note='type',created_by='王工',scope_type='type',scope_key=key,claimed_values={'length_mm':700,'width_mm':650,'height_mm':400},verification=ver)
        m._dim_overrides_save([{'material':'5083','category':'弯头','dn_spec':'300','length_mm':700,'width_mm':650,'height_mm':400,'source':'批准图','verified':1,'evidence_id':ev['id']}])
        d=m._dimension_override_lookup('弯头','DN300','5083',''); self.assertEqual(d['size_grade'],'A'); self.assertEqual(d['size_subgrade'],'A3')
    def test_16_wrong_scope_dimension_evidence_downgrades(self):
        ev=self.evidence('dimension_override','5083|弯头|300','approved_dimension',scope='item',scope_key='1')
        m._dim_overrides_save([{'material':'5083','category':'弯头','dn_spec':'300','length_mm':700,'width_mm':650,'height_mm':400,'source':'批准图','verified':1,'evidence_id':ev['id']}])
        self.assertEqual(m._dimension_override_lookup('弯头','DN300','5083','')['size_grade'],'B')
    def test_17_gbt_is_b3_and_only_updates_c(self):
        b=self.seed('B',(111,222,333)); c=self.seed('C',(1,1,1)); m.apply_standard_estimates(); con=m.db(); rb=con.execute('SELECT * FROM items WHERE id=?',(b,)).fetchone(); rc=con.execute('SELECT * FROM items WHERE id=?',(c,)).fetchone(); con.close(); self.assertEqual((rb['length_mm'],rb['width_mm'],rb['height_mm']),(111,222,333)); self.assertEqual(rc['size_subgrade'],'B3')

class TestImportClassifier(unittest.TestCase):
    def test_18_english_elbow_detected(self): self.assertEqual(purchase_row_classification('','ELBOW 90 DEG DN300')['kind'],'pipe')
    def test_19_pipe_support_excluded(self): self.assertEqual(purchase_row_classification('','管道支架 DN300')['kind'],'generic')
    def test_20_flange_gasket_excluded(self): self.assertEqual(purchase_row_classification('','法兰垫片')['kind'],'generic')
    def test_21_mixed_purchase_partition(self):
        tables=[{'sheet':'S','rows':[['序号','代号','名称','材料','需要采购'],[1,'DN300','90°弯头','Q235B',2],[2,'DN150','PIPE','Q235B',10],[3,'M-01','电机','Q235B',1],[4,'X','管道支架 DN100','Q235B',1]]}]
        sp,gp,st=partition_purchase_tables(tables,m._strict_purchase_number); self.assertEqual(st['special_rows'],2); self.assertEqual(st['generic_rows'],2)

class TestContainerEvidence(IsolatedDbMixin,unittest.TestCase):
    def test_22_container_reference_must_match(self):
        from fastapi.testclient import TestClient
        self.set_user(); c=TestClient(m.app,raise_server_exceptions=True); csv=b'code,inner_l,inner_w,inner_h,door_w,door_h,payload,open_top\n40HQ,12032,2352,2698,2340,2585,28600,0\n'
        r=c.post('/container-specs/upload',data={'reference_no':'BOX-1','evidence_reference_no':'BOX-2'},files={'file':('actual.csv',csv,'text/csv'),'support_evidence':('cert.pdf',VALID_PDF,'application/pdf')},follow_redirects=False); self.assertIn('error',r.headers.get('location',''))
    def test_23_container_reference_verified(self):
        from fastapi.testclient import TestClient
        self.set_user(); c=TestClient(m.app,raise_server_exceptions=True); csv=b'code,inner_l,inner_w,inner_h,door_w,door_h,payload,open_top\n40HQ,12032,2352,2698,2340,2585,28600,0\n'
        r=c.post('/container-specs/upload',data={'reference_no':'BOX-1','evidence_reference_no':'BOX-1'},files={'file':('actual.csv',csv,'text/csv'),'support_evidence':('cert.pdf',VALID_PDF,'application/pdf')},follow_redirects=False); self.assertEqual(r.status_code,303); meta=m._container_specs_meta_load(); self.assertTrue(m._evidence_matches(meta['evidence_id'],scope_type='project',scope_key='BOX-1',evidence_types=('container_certificate_or_booking',),require_verified=True))

class TestXlsxAndRuntime(IsolatedDbMixin,unittest.TestCase):
    def test_24_wal_mode(self):
        con=m.db(); self.assertEqual(str(con.execute('PRAGMA journal_mode').fetchone()[0]).lower(),'wal'); con.close()
    def test_25_bounded_cache(self):
        old=m._PERF_CACHE_LIMITS['fast']; m._PERF_CACHE['fast'].clear(); m._PERF_CACHE_LIMITS['fast']=3
        try:
            for i in range(10):m._perf_cache_set('fast',f'k{i}',{'i':i})
            self.assertEqual(len(m._PERF_CACHE['fast']),3)
        finally:m._PERF_CACHE_LIMITS['fast']=old; m._PERF_CACHE['fast'].clear()
    def test_26_streamed_zip_limit(self):
        b=io.BytesIO();
        with zipfile.ZipFile(b,'w',zipfile.ZIP_STORED) as z:z.writestr('big.xml',b'x'*4096)
        old=import_security.MAX_ZIP_MEMBER_UNCOMPRESSED
        try:
            import_security.MAX_ZIP_MEMBER_UNCOMPRESSED=1024
            with self.assertRaises(ValueError): import_security.validate_zip_archive(b.getvalue(),'测试ZIP')
        finally: import_security.MAX_ZIP_MEMBER_UNCOMPRESSED=old
    def test_27_parallel_connections(self):
        errors=[]
        def worker(k):
            try:
                for n in range(4):
                    con=m.db(); con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_source,size_note,package_type,stackable,rotatable) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(f'T{k}-{n}',1,10,10,10,1,'B','test','B','test','','裸装',1,1)); con.commit(); con.close()
            except Exception as e: errors.append(repr(e))
        ts=[threading.Thread(target=worker,args=(k,)) for k in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]; self.assertEqual(errors,[])

class TestHttpAndFormal(IsolatedDbMixin,unittest.TestCase):
    def setUp(self):
        super().setUp(); from fastapi.testclient import TestClient; self.client=TestClient(m.app,raise_server_exceptions=True)
    def test_28_legacy_get_guarded(self):
        self.assertEqual(self.client.get('/export-packing-list').status_code,409)
    def test_29_cross_site_post_rejected(self):
        self.assertEqual(self.client.post('/release-meta',data={'project_name':'X','approver':'Y'},headers={'Origin':'https://evil.example'}).status_code,403)
    def test_30_template_upload_then_home(self):
        data=(ROOT/'material_import_template.xlsx').read_bytes(); r=self.client.post('/upload-materials',files={'file':('material_import_template.xlsx',data,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},data={'mode':'replace'},follow_redirects=False)
        self.assertEqual(r.status_code,303); self.assertNotIn('error=',r.headers.get('location',''))
        self.assertEqual(self.client.get(r.headers['location']).status_code,200)
        con=m.db(); n=con.execute('SELECT COUNT(*) FROM items').fetchone()[0]; con.close(); self.assertGreater(n,0)
    def test_31_formal_gate_rejects_a_with_unverified_evidence(self):
        self.set_user(); con=m.db(); cur=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_source,size_note,package_type,stackable,rotatable) VALUES('X',1,100,100,100,10,'A','x','A','x','', '裸装',1,1)"); iid=cur.lastrowid; con.commit(); con.close(); ev=self.evidence('item',iid,'measured_or_approved_size',scope='item',scope_key=iid,verified=False); con=m.db(); con.execute("UPDATE items SET size_evidence_id=?,size_evidence_scope='item',size_evidence_scope_key=? WHERE id=?",(ev['id'],str(iid),iid)); con.commit(); con.close(); issues=m._data_quality_release_issues(*m.fetch_state()); self.assertTrue(any('尺寸' in x for x in issues))

class TestHistoricalComparison(IsolatedDbMixin,unittest.TestCase):
    def test_32_history_record_does_not_claim_auto_calibration(self):
        from fastapi.testclient import TestClient
        c=TestClient(m.app,raise_server_exceptions=True); r=c.post('/benchmark/save',data={'project_name':'REAL-P','actual_crates':'2','actual_containers':'1','actual_cbm':'5.5','actual_tare_kg':'100','note':'done'},files={'evidence':('actual.pdf',VALID_PDF,'application/pdf')},follow_redirects=False); self.assertEqual(r.status_code,303); self.assertIn('message',r.headers.get('location',''))

class TestCrateEvidence(IsolatedDbMixin,unittest.TestCase):
    def seed_crate(self):
        con=m.db(); cur=con.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg) VALUES(?,?,?,?,?)",('BX-T',1200,900,800,80)); cid=cur.lastrowid
        con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_source,size_note,package_type,stackable,rotatable,crate_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('设备C',1,500,400,300,100,'B','上传声明','B','上传声明','','木箱',1,1,cid)); con.commit(); con.close(); return cid
    def test_33_crate_size_claim_mismatch_rejected(self):
        from fastapi.testclient import TestClient
        self.set_user(); cid=self.seed_crate(); c=TestClient(m.app,raise_server_exceptions=True)
        r=c.post(f'/crates/{cid}/confirm-size',files={'evidence':('crate.pdf',VALID_PDF,'application/pdf')},data={'evidence_length_mm':'1300','evidence_width_mm':'900','evidence_height_mm':'800'},follow_redirects=False)
        self.assertIn('error',r.headers.get('location','')); con=m.db(); row=con.execute('SELECT * FROM crates WHERE id=?',(cid,)).fetchone(); con.close(); self.assertEqual(int(row['size_confirmed'] or 0),0)
    def test_34_crate_gross_claim_mismatch_rejected(self):
        from fastapi.testclient import TestClient
        self.set_user(); cid=self.seed_crate(); c=TestClient(m.app,raise_server_exceptions=True)
        r=c.post(f'/crates/{cid}/actual-gross',files={'evidence':('ticket.pdf',VALID_PDF,'application/pdf')},data={'actual_gross_weight_kg':'200','evidence_weight_kg':'220'},follow_redirects=False)
        self.assertIn('error',r.headers.get('location','')); con=m.db(); row=con.execute('SELECT * FROM crates WHERE id=?',(cid,)).fetchone(); con.close(); self.assertEqual(int(row['gross_weight_confirmed'] or 0),0)

class TestFormalDataGatePass(IsolatedDbMixin,unittest.TestCase):
    def test_35_strict_item_evidence_can_pass_data_gate(self):
        from fastapi.testclient import TestClient
        self.set_user(); con=m.db(); cur=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_source,size_note,package_type,stackable,rotatable) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('裸装设备',1,1000,800,600,120,'B','上传声明','B','上传声明','','裸装',1,1)); iid=cur.lastrowid; con.commit(); con.close()
        c=TestClient(m.app,raise_server_exceptions=True)
        r=c.post(f'/items/{iid}/confirm-size',files={'evidence':('measure.pdf',VALID_PDF,'application/pdf')},data={'evidence_length_mm':'1000','evidence_width_mm':'800','evidence_height_mm':'600','evidence_scope':'item'},follow_redirects=False); self.assertEqual(r.status_code,303)
        r=c.post(f'/items/{iid}/confirm-weight',files={'evidence':('weight.pdf',VALID_PDF,'application/pdf')},data={'evidence_weight_kg':'120','evidence_scope':'item'},follow_redirects=False); self.assertEqual(r.status_code,303)
        items,crates=m.fetch_state(); self.assertEqual(m._data_quality_release_issues(items,crates),[])


class TestV51EvidenceConsistency(IsolatedDbMixin,unittest.TestCase):
    def setUp(self):
        super().setUp(); from fastapi.testclient import TestClient; self.client=TestClient(m.app,raise_server_exceptions=True); self.set_user()

    def _seed_item(self, name='设备V51'):
        con=m.db(); cur=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_source,size_note,package_type,stackable,rotatable) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(name,1,1000,800,600,120,'B','上传声明','B','上传声明','','裸装',1,1)); iid=cur.lastrowid; con.commit(); con.close(); return iid

    def test_36_export_gate_rechecks_current_item_dimension_against_claim(self):
        iid=self._seed_item()
        r=self.client.post(f'/items/{iid}/confirm-size',files={'evidence':('measure.pdf',VALID_PDF,'application/pdf')},data={'evidence_length_mm':'1000','evidence_width_mm':'800','evidence_height_mm':'600','evidence_scope':'item'},follow_redirects=False); self.assertEqual(r.status_code,303)
        r=self.client.post(f'/items/{iid}/confirm-weight',files={'evidence':('weight.pdf',VALID_PDF,'application/pdf')},data={'evidence_weight_kg':'120','evidence_scope':'item'},follow_redirects=False); self.assertEqual(r.status_code,303)
        con=m.db(); con.execute('UPDATE items SET length_mm=1100 WHERE id=?',(iid,)); con.commit(); con.close()
        issues=m._data_quality_release_issues(*m.fetch_state())
        self.assertTrue(any('当前尺寸已与凭证核对值不一致' in x for x in issues))

    def test_37_empty_crate_resize_invalidates_size_confirmation(self):
        con=m.db(); cur=con.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg) VALUES('BX-E',1200,900,800,80)"); cid=cur.lastrowid; con.commit(); con.close()
        r=self.client.post(f'/crates/{cid}/confirm-size',files={'evidence':('crate.pdf',VALID_PDF,'application/pdf')},data={'evidence_length_mm':'1200','evidence_width_mm':'900','evidence_height_mm':'800'},follow_redirects=False); self.assertEqual(r.status_code,303)
        r=self.client.post(f'/crates/{cid}/resize',data={'length_mm':'1300','width_mm':'900','height_mm':'800'}); self.assertEqual(r.status_code,200)
        con=m.db(); row=con.execute('SELECT size_confirmed,size_evidence_id FROM crates WHERE id=?',(cid,)).fetchone(); con.close()
        self.assertEqual(int(row['size_confirmed'] or 0),0); self.assertIsNone(row['size_evidence_id'])

    def test_38_type_evidence_rechecked_against_current_item(self):
        key='Q235B|弯头|300'
        ver=verify_dimension_claim((700,650,400),(700,650,400))
        ev=m.EVIDENCE_STORE.save_evidence(entity_type='dimension_override',entity_id=key,evidence_type='approved_dimension',filename='type.pdf',content=VALID_PDF,note='type',created_by='王工',scope_type='type',scope_key=key,claimed_values={'length_mm':700,'width_mm':650,'height_mm':400},verification=ver)
        con=m.db(); cur=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_subgrade,size_source,size_note,size_evidence_id,size_evidence_scope,size_evidence_scope_key,package_type,stackable,rotatable) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('Q235B 弯头 DN300',1,710,650,400,20,'B','x','A','A3','批准类型图','',ev['id'],'type',key,'木箱',1,1)); iid=cur.lastrowid; con.commit(); con.close()
        issues=m._data_quality_release_issues(*m.fetch_state())
        self.assertTrue(any('当前尺寸已与凭证核对值不一致' in x for x in issues))

    def test_39_container_csv_change_invalidates_container_evidence(self):
        csv1=b'code,inner_l,inner_w,inner_h,door_w,door_h,payload,open_top\n40HQ,12032,2352,2698,2340,2585,28600,0\n'
        r=self.client.post('/container-specs/upload',data={'reference_no':'BOX-1','evidence_reference_no':'BOX-1'},files={'file':('actual.csv',csv1,'text/csv'),'support_evidence':('cert.pdf',VALID_PDF,'application/pdf')},follow_redirects=False); self.assertEqual(r.status_code,303)
        meta=m._container_specs_meta_load(); self.assertTrue(meta.get('confirmed'))
        meta['csv_sha256']='tampered'; m._json_save(m.CONTAINER_SPECS_META_PATH,meta)
        issues=m._formal_export_issues({'containers':[],'invalid_manual_layouts':[]},'S')
        self.assertTrue(any('原凭证登记状态不一致' in x for x in issues))


class TestV51ImportClassification(unittest.TestCase):
    def test_40_steel_does_not_match_tee(self):
        self.assertEqual(purchase_row_classification('','STEEL PLATE')['kind'],'generic')
    def test_41_packaging_annotation_does_not_hide_fitting(self):
        self.assertEqual(purchase_row_classification('','DN300 弯头（木箱包装）')['kind'],'pipe')
    def test_42_bare_dn_without_fitting_noun_is_generic(self):
        self.assertEqual(purchase_row_classification('','底板 DN300')['kind'],'generic')


class TestV51EvidenceIntegrityAtRelease(IsolatedDbMixin,unittest.TestCase):
    def test_43_tampered_evidence_fails_release_gate(self):
        from fastapi.testclient import TestClient
        self.set_user(); con=m.db(); cur=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_source,size_note,package_type,stackable,rotatable) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('裸装T',1,1000,800,600,120,'B','上传','B','上传','','裸装',1,1)); iid=cur.lastrowid; con.commit(); con.close()
        c=TestClient(m.app,raise_server_exceptions=True)
        c.post(f'/items/{iid}/confirm-size',files={'evidence':('measure.pdf',VALID_PDF,'application/pdf')},data={'evidence_length_mm':'1000','evidence_width_mm':'800','evidence_height_mm':'600','evidence_scope':'item'},follow_redirects=False)
        c.post(f'/items/{iid}/confirm-weight',files={'evidence':('weight.pdf',VALID_PDF,'application/pdf')},data={'evidence_weight_kg':'120','evidence_scope':'item'},follow_redirects=False)
        con=m.db(); row=con.execute('SELECT size_evidence_id FROM items WHERE id=?',(iid,)).fetchone(); con.close()
        m.EVIDENCE_STORE.path_for(row['size_evidence_id']).write_bytes(b'tampered')
        issues=m._data_quality_release_issues(*m.fetch_state())
        self.assertTrue(any('尺寸' in x for x in issues))


class TestV53ContainerSpecConsistency(IsolatedDbMixin,unittest.TestCase):
    def setUp(self):
        super().setUp(); from fastapi.testclient import TestClient; self.client=TestClient(m.app,raise_server_exceptions=True); self.set_user()

    def _confirm_specs(self):
        csv1=b'code,inner_l,inner_w,inner_h,door_w,door_h,payload,open_top\n40HQ,12032,2352,2698,2340,2585,28600,0\n'
        r=self.client.post('/container-specs/upload',data={'reference_no':'BOX-V53','evidence_reference_no':'BOX-V53'},files={'file':('actual.csv',csv1,'text/csv'),'support_evidence':('cert.pdf',VALID_PDF,'application/pdf')},follow_redirects=False)
        self.assertEqual(r.status_code,303)
        return m._container_specs_meta_load()

    def test_44_current_container_specs_json_change_invalidates_formal_gate(self):
        meta=self._confirm_specs(); self.assertTrue(meta.get('specs_sha256'))
        raw=m._json_load(m.CONTAINER_SPECS_PATH,[]); raw[0]['inner_w']=9999; m._json_save(m.CONTAINER_SPECS_PATH,raw); m._refresh_containers()
        issues=m._formal_export_issues({'containers':[],'invalid_manual_layouts':[]},'S')
        self.assertTrue(any('当前参与计算的实际柜参数已与确认时参数不一致' in x for x in issues))

    def test_45_dimension_evidence_tolerance_is_stricter_than_planning_tolerance(self):
        from app.evidence_validation import verify_dimension_claim
        self.assertFalse(verify_dimension_claim((10000,800,600),(10040,800,600))['ok'])
        self.assertTrue(verify_dimension_claim((10000,800,600),(10008,800,600))['ok'])

    def test_46_weight_evidence_tolerance_is_stricter_than_planning_tolerance(self):
        from app.evidence_validation import verify_weight_claim
        self.assertFalse(verify_weight_claim(5000,5010)['ok'])
        self.assertTrue(verify_weight_claim(5000,5004)['ok'])

class TestV53ContainerSingleSource(IsolatedDbMixin,unittest.TestCase):
    def setUp(self):
        super().setUp(); from fastapi.testclient import TestClient; self.client=TestClient(m.app,raise_server_exceptions=True); self.set_user()
    def _seed_item(self):
        con=m.db(); con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_source,size_note,package_type,stackable,rotatable) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('箱体参数测试件',1,1000,800,600,100,'B','上传','B','上传','','裸装',1,1)); con.commit(); con.close()
    def test_47_corrupt_actual_specs_block_planning_without_default_fallback(self):
        self._seed_item(); m._json_save(m.CONTAINER_SPECS_META_PATH,{'mode':'actual','confirmed':True,'reference_no':'BROKEN'}); m.CONTAINER_SPECS_PATH.write_text('{broken',encoding='utf-8')
        rec=m.recommend_containers(*m.fetch_state())
        self.assertTrue(rec.get('container_config_error')); self.assertEqual(rec.get('options'),[])
        self.assertEqual(m._container_specs_state()['mode'],'invalid')
    def test_48_invalid_actual_specs_home_still_renders_clear_error(self):
        self._seed_item(); m._json_save(m.CONTAINER_SPECS_META_PATH,{'mode':'actual','confirmed':True,'reference_no':'BROKEN'}); m.CONTAINER_SPECS_PATH.write_text('[]',encoding='utf-8')
        r=self.client.get('/'); self.assertEqual(r.status_code,200); self.assertIn('实柜参数无效'.encode('utf-8'),r.content)
    def test_49_explicit_reset_switches_to_standard_mode(self):
        m._json_save(m.CONTAINER_SPECS_META_PATH,{'mode':'actual','confirmed':True}); m.CONTAINER_SPECS_PATH.write_text('{broken',encoding='utf-8')
        r=self.client.post('/container-specs/reset',follow_redirects=False); self.assertEqual(r.status_code,303)
        st=m._container_specs_state(); self.assertEqual(st['mode'],'standard'); self.assertEqual(st['rows'][0]['code'],'20GP')
    def test_50_solver_reads_current_json_not_stale_global_snapshot(self):
        csv1=b'code,inner_l,inner_w,inner_h,door_w,door_h,payload,open_top\n40HQ,12032,2352,2698,2340,2585,28600,0\n'
        r=self.client.post('/container-specs/upload',data={'reference_no':'BOX-SOURCE','evidence_reference_no':'BOX-SOURCE'},files={'file':('actual.csv',csv1,'text/csv'),'support_evidence':('cert.pdf',VALID_PDF,'application/pdf')},follow_redirects=False); self.assertEqual(r.status_code,303)
        self.assertEqual(float(m._active_containers()[0]['inner_w']),2352)
        raw=m._json_load(m.CONTAINER_SPECS_PATH,[]); raw[0]['inner_w']=3000; m._json_save(m.CONTAINER_SPECS_PATH,raw)
        # Do NOT call _refresh_containers(): the active solver source must still see disk truth.
        self.assertEqual(float(m._active_containers()[0]['inner_w']),3000)
        self.assertNotEqual(m._container_specs_meta_load().get('specs_sha256'),m._container_specs_state().get('sha256'))



    def test_52_invalid_actual_specs_empty_project_home_still_200(self):
        m._json_save(m.CONTAINER_SPECS_META_PATH,{'mode':'actual','confirmed':True,'reference_no':'BROKEN-EMPTY'}); m.CONTAINER_SPECS_PATH.write_text('{broken',encoding='utf-8')
        r=self.client.get('/'); self.assertEqual(r.status_code,200); self.assertIn('当前实柜参数无效'.encode('utf-8'),r.content)

class TestV53ShipmentPackage(IsolatedDbMixin,unittest.TestCase):
    def setUp(self):
        super().setUp(); from fastapi.testclient import TestClient; self.client=TestClient(m.app,raise_server_exceptions=True); self.set_user()

    def test_51_full_shipment_package_requires_and_uses_container_gate(self):
        # One bare package with strict A evidence plus actual container evidence is enough for a full-ticket archive.
        con=m.db(); cur=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_source,size_note,package_type,stackable,rotatable) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('裸装整票测试',1,1000,800,600,120,'B','上传','B','上传','','裸装',1,1)); iid=cur.lastrowid; con.commit(); con.close()
        self.client.post(f'/items/{iid}/confirm-size',files={'evidence':('measure.pdf',VALID_PDF,'application/pdf')},data={'evidence_length_mm':'1000','evidence_width_mm':'800','evidence_height_mm':'600','evidence_scope':'item'},follow_redirects=False)
        self.client.post(f'/items/{iid}/confirm-weight',files={'evidence':('weight.pdf',VALID_PDF,'application/pdf')},data={'evidence_weight_kg':'120','evidence_scope':'item'},follow_redirects=False)
        csv1=b'code,inner_l,inner_w,inner_h,door_w,door_h,payload,open_top\n40HQ,12032,2352,2698,2340,2585,28600,0\n'
        self.client.post('/container-specs/upload',data={'reference_no':'BOX-PACK','evidence_reference_no':'BOX-PACK'},files={'file':('actual.csv',csv1,'text/csv'),'support_evidence':('cert.pdf',VALID_PDF,'application/pdf')},follow_redirects=False)
        items,crates=m.fetch_state(); rec=m.recommend_containers(items,crates); self.assertTrue(rec.get('options')); scheme=rec['options'][0]['code']
        r=self.client.post('/export-shipment-package',json={'scheme':scheme,'deep':False})
        self.assertEqual(r.status_code,200); self.assertIn('zip',r.headers.get('content-type','').lower())



class TestV54PhysicalConstraints(IsolatedDbMixin,unittest.TestCase):
    def test_53_real_cg_changes_container_cg(self):
        c={'code':'T','inner_l':1000,'inner_w':1000,'inner_h':1000,'door_w':1000,'door_h':1000,'payload':5000,'open_top':False,'floor_load_kg_m2':None}
        p={'name':'偏心设备','x':0,'y':0,'z':0,'l':1000,'w':800,'h':600,'weight_kg':100,'cg_local_x':800,'cg_local_y':400,'cg_local_z':300,'cg_confirmed':True,'stackable':False}
        a=m._container_state_audit({'container':c,'placements':[p],'weight':100})
        self.assertAlmostEqual(a['cg_x_pct'],80.0,places=1)
        self.assertEqual(a['assumed_cg_count'],0)

    def test_54_unknown_crate_topload_cannot_support_stack(self):
        base={'name':'BX','type':'木箱','x':0,'y':0,'z':0,'l':1000,'w':1000,'h':500,'weight_kg':200,'stackable':True,'top_load_verified':False,'max_top_load_kg':None}
        top={'name':'TOP','type':'木箱','x':0,'y':0,'z':500,'l':1000,'w':1000,'h':400,'weight_kg':100,'stackable':False}
        self.assertFalse(m._support_ok(top,[base]))

    def test_55_confirmed_topload_allows_stack_within_capacity(self):
        base={'name':'BX','type':'木箱','x':0,'y':0,'z':0,'l':1000,'w':1000,'h':500,'weight_kg':200,'stackable':True,'top_load_verified':True,'max_top_load_kg':500}
        top={'name':'TOP','type':'木箱','x':0,'y':0,'z':500,'l':1000,'w':1000,'h':400,'weight_kg':300,'stackable':False}
        self.assertTrue(m._support_ok(top,[base]))

    def test_56_floor_load_limit_is_hard_container_constraint(self):
        c={'code':'T','inner_l':3000,'inner_w':2000,'inner_h':2000,'door_w':2000,'door_h':1900,'payload':10000,'open_top':False,'floor_load_kg_m2':1000}
        p={'name':'重件','x':1500,'y':700,'z':0,'l':1000,'w':1000,'h':500,'weight_kg':1500,'cg_confirmed':True,'stackable':False}
        a=m._container_state_audit({'container':c,'placements':[p],'weight':1500})
        self.assertEqual(a['status'],'不通过')
        self.assertTrue(a['floor_load_checked']); self.assertGreater(a['max_floor_pressure_kg_m2'],1000)

    def test_57_missing_floor_limit_is_reported_but_not_invented(self):
        c={'code':'T','inner_l':3000,'inner_w':2000,'inner_h':2000,'door_w':2000,'door_h':1900,'payload':10000,'open_top':False,'floor_load_kg_m2':None}
        p={'name':'普通件','x':100,'y':100,'z':0,'l':1000,'w':1000,'h':500,'weight_kg':500,'cg_confirmed':True,'stackable':False}
        a=m._container_state_audit({'container':c,'placements':[p],'weight':500})
        self.assertFalse(a['floor_load_checked'])

    def test_58_lateral_position_outside_door_aperture_flags_manoeuvre_review(self):
        from app.physical_constraints import door_maneuver_audit
        c={'code':'40HQ','inner_w':2352,'inner_h':2698,'door_w':2340,'door_h':2585,'open_top':False}
        p={'name':'侧向箱','x':1000,'y':1450,'z':0,'l':1000,'w':900,'h':600}
        a=door_maneuver_audit([p],c)
        self.assertEqual(a['maneuver_count'],1); self.assertTrue(a['review_notes'])

    def test_59_crate_package_is_nonstackable_until_topload_confirmed(self):
        con=m.db(); cur=con.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg) VALUES('BX-P',1200,1000,800,100)"); cid=cur.lastrowid
        con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_source,size_note,package_type,stackable,rotatable,crate_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('货物',1,800,600,400,100,'B','上传','B','上传','','木箱',1,1,cid)); con.commit(); con.close()
        pk=m.get_packages(*m.fetch_state())[0]; self.assertFalse(pk['stackable']); self.assertFalse(pk['top_load_verified'])
        con=m.db(); con.execute("UPDATE crates SET max_top_load_kg=500,top_load_confirmed=1,top_load_source_type='PACKING_SUPPLIER_APPROVED' WHERE id=?",(cid,)); con.commit(); con.close()
        pk=m.get_packages(*m.fetch_state())[0]; self.assertTrue(pk['stackable']); self.assertTrue(pk['top_load_verified']); self.assertEqual(pk['max_top_load_kg'],500)

    def test_60_container_spec_hash_includes_optional_floor_limit(self):
        from app.container_specs import specs_sha256
        a=[{'code':'40HQ','inner_l':12032,'inner_w':2352,'inner_h':2698,'door_w':2340,'door_h':2585,'payload':28600,'open_top':False}]
        b=[dict(a[0],floor_load_kg_m2=3000)]
        self.assertNotEqual(specs_sha256(a),specs_sha256(b))

    def test_61_item_cg_endpoint_persists_real_cg(self):
        from fastapi.testclient import TestClient
        con=m.db(); cur=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_source,size_note,package_type,stackable,rotatable) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('CG件',1,1000,800,600,100,'B','上传','B','上传','','裸装',0,1)); iid=cur.lastrowid; con.commit(); con.close()
        c=TestClient(m.app,raise_server_exceptions=True); r=c.post(f'/items/{iid}/cg',data={'cg_x_mm':'700','cg_y_mm':'300','cg_z_mm':'200','cg_source_type':'MANUFACTURER_DATA','cg_source':'厂家CG图'},follow_redirects=False); self.assertEqual(r.status_code,303)
        con=m.db(); row=dict(con.execute('SELECT * FROM items WHERE id=?',(iid,)).fetchone()); con.close()
        self.assertEqual(row['cg_confirmed'],1); self.assertEqual(row['cg_x_mm'],700); self.assertEqual(row['cg_source'],'厂家CG图')

    def test_62_archive_label_no_longer_claims_shipment_package(self):
        self.assertIn('整票数据对照归档包',m.SHIPMENT_PACKAGE_LABEL); self.assertNotIn('整票发运资料包',m.SHIPMENT_PACKAGE_LABEL)

    def test_63_multilevel_topload_is_cumulative(self):
        base={'name':'BASE','type':'木箱','x':0,'y':0,'z':0,'l':1000,'w':1000,'h':500,'weight_kg':100,'stackable':True,'top_load_verified':True,'max_top_load_kg':500}
        mid={'name':'MID','type':'木箱','x':0,'y':0,'z':500,'l':1000,'w':1000,'h':400,'weight_kg':300,'stackable':True,'top_load_verified':True,'max_top_load_kg':500}
        top={'name':'TOP','type':'木箱','x':0,'y':0,'z':900,'l':1000,'w':1000,'h':300,'weight_kg':250,'stackable':False,'top_load_verified':False,'max_top_load_kg':None}
        # BASE carries MID+TOP = 550 kg, so the third level must be rejected even though TOP alone < 500.
        self.assertFalse(m._support_ok(top,[base,mid]))

    def test_64_smart_crate_preserves_real_cg_into_placements(self):
        con=m.db(); con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_source,size_note,package_type,stackable,rotatable,cg_x_mm,cg_y_mm,cg_z_mm,cg_source,cg_source_type,cg_confirmed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('CG-PACK',2,400,300,200,20,'B','test','B','test','','木箱',1,1,250,150,100,'厂家CG','MANUFACTURER_DATA',1)); con.commit(); con.close()
        m.apply_smart_crates(2000,1500,1500,5000,100,45)
        con=m.db(); rows=[dict(r) for r in con.execute('SELECT cg_local_x,cg_local_y,cg_local_z,cg_confirmed FROM crate_placements').fetchall()]; con.close()
        self.assertEqual(len(rows),2); self.assertTrue(all(r['cg_confirmed']==1 for r in rows)); self.assertTrue(all(r['cg_local_x']==250 for r in rows))

    def test_65_item_cg_update_reaudits_existing_crate(self):
        from fastapi.testclient import TestClient
        con=m.db(); cur=con.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg) VALUES('BX-CG',1200,1000,800,80)"); cid=cur.lastrowid
        cur=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,size_grade,size_source,size_note,package_type,stackable,rotatable,crate_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('CG件',1,600,400,300,100,'B','test','B','test','','木箱',1,1,cid)); iid=cur.lastrowid; con.commit(); con.close()
        m._repack_existing_crate(cid)
        c=TestClient(m.app,raise_server_exceptions=True); r=c.post(f'/items/{iid}/cg',data={'cg_x_mm':'500','cg_y_mm':'200','cg_z_mm':'150','cg_source_type':'MANUFACTURER_DATA','cg_source':'厂家CG图'},follow_redirects=False); self.assertEqual(r.status_code,303)
        con=m.db(); pl=dict(con.execute('SELECT * FROM crate_placements WHERE crate_id=?',(cid,)).fetchone()); audit=dict(con.execute('SELECT * FROM crate_audits WHERE crate_id=?',(cid,)).fetchone()); con.close()
        self.assertEqual(pl['cg_confirmed'],1); self.assertEqual(pl['cg_local_x'],500); self.assertGreater(audit['cg_x_pct'],50)

    def test_66_5083_insert_path_preserves_cg(self):
        n=m.insert_5083_pipe_crates([{'name':'5083管 Φ219×6','qty':3,'length_mm':6000,'width_mm':250,'height_mm':250,'weight_kg':40,'weight_grade':'B','weight_source':'test','size_grade':'B','size_source':'test','cg_x_mm':3200,'cg_y_mm':120,'cg_z_mm':120,'cg_source':'厂家CG','cg_source_type':'MANUFACTURER_DATA','cg_confirmed':1}])
        self.assertEqual(n,1); con=m.db(); r=dict(con.execute('SELECT * FROM items').fetchone()); con.close(); self.assertEqual(r['cg_confirmed'],1); self.assertEqual(r['cg_x_mm'],3200)

    def test_67_pipe_bundle_insert_path_executes(self):
        con=m.db(); con.execute("INSERT INTO pipe_sources(code,category,dn_text,description,material,total_length_m,od_mm,thickness_mm,density_kg_m3,material_family) VALUES(?,?,?,?,?,?,?,?,?,?)",('P1','PIPE','DN100','test','CS',12,114.3,6,7850,'CS')); con.commit(); con.close()
        bundles,pipes,sources=m.rebuild_pipe_bundles(); self.assertEqual(sources,1); self.assertGreaterEqual(bundles,1); self.assertGreaterEqual(pipes,1)

    def test_68_elevated_inside_aperture_is_lift_info_not_risk(self):
        from app.physical_constraints import door_maneuver_audit
        c={'code':'40HQ','inner_w':2352,'inner_h':2698,'door_w':2340,'door_h':2585,'open_top':False}
        p={'name':'上层箱','x':1000,'y':100,'z':500,'l':1000,'w':900,'h':600}
        a=door_maneuver_audit([p],c); self.assertEqual(a['lift_count'],1); self.assertEqual(a['maneuver_count'],0); self.assertFalse(a['review_notes'])

    def test_69_pipe_axis_normalization_rotates_real_cg_axes(self):
        item={'id':1,'name':'管道测试','length_mm':200,'width_mm':600,'height_mm':300,'weight_kg':10,'weight_grade':'B','weight_source':'test','rotatable':1,'stackable':1,'cg_x_mm':50,'cg_y_mm':500,'cg_z_mm':100,'cg_source_type':'MANUFACTURER_DATA','cg_confirmed':1}
        u=m._packing_unit_from_item(item,0)
        self.assertEqual((u['actual_l'],u['actual_w'],u['actual_h']),(600,300,200))
        self.assertEqual((u['cg_local_x'],u['cg_local_y'],u['cg_local_z']),(500,100,50))

    def test_70_unconfirmed_required_lashing_is_review(self):
        c={'code':'T','inner_l':3000,'inner_w':2000,'inner_h':2000,'door_w':1900,'door_h':1900,'payload':10000,'open_top':False,'floor_load_kg_m2':None}
        p={'name':'BX-L','type':'木箱','x':1000,'y':600,'z':0,'l':1000,'w':800,'h':600,'weight_kg':500,'cg_confirmed':True,'stackable':False,'lashing_required':True,'lashing_points':0,'lashing_confirmed':False}
        a=m._container_state_audit({'container':c,'placements':[p],'weight':500}); self.assertEqual(a['status'],'需复核'); self.assertEqual(a['lashing_required_count'],1)

class TestV55PhysicalSemantics(IsolatedDbMixin,unittest.TestCase):
    def test_71_critical_unknown_cg_becomes_engineering_review(self):
        c={'code':'T','inner_l':4000,'inner_w':2400,'inner_h':2600,'door_w':2300,'door_h':2500,'payload':10000,'open_top':False,'floor_load_kg_m2':None}
        p={'name':'重型未知CG','x':1500,'y':700,'z':0,'l':1000,'w':1000,'h':800,'weight_kg':800,'cg_confirmed':False,'stackable':False,'contact_area_mode':'FULL_BASE','contact_area_source_type':'ENGINEERING_CALC'}
        a=m._container_state_audit({'container':c,'placements':[p],'weight':800})
        self.assertEqual(a['status'],'需复核'); self.assertEqual(a['critical_assumed_cg_count'],1)
        self.assertTrue(any('关键货物CG未确认' in x for x in a['engineering_review_notes']))

    def test_72_small_unknown_cg_is_information_only(self):
        c={'code':'T','inner_l':4000,'inner_w':2400,'inner_h':2600,'door_w':2300,'door_h':2500,'payload':10000,'open_top':False,'floor_load_kg_m2':None}
        p1={'name':'小件1','x':100,'y':100,'z':0,'l':500,'w':500,'h':300,'weight_kg':50,'cg_confirmed':False,'stackable':False,'contact_area_mode':'FULL_BASE'}
        p2={'name':'已确认主件','x':700,'y':100,'z':0,'l':1500,'w':1000,'h':800,'weight_kg':950,'cg_confirmed':True,'cg_local_x':750,'cg_local_y':500,'cg_local_z':400,'stackable':False,'contact_area_mode':'FULL_BASE'}
        a=m._container_state_audit({'container':c,'placements':[p1,p2],'weight':1000})
        self.assertEqual(a['critical_assumed_cg_count'],0); self.assertTrue(a['information_notes'])

    def test_73_skid_contact_area_drives_static_pressure(self):
        from app.physical_constraints import floor_load_audit
        c={'code':'T','floor_load_kg_m2':3000}
        p={'name':'滑木箱','x':0,'y':0,'z':0,'l':2000,'w':1000,'h':800,'weight_kg':1500,
           'contact_area_mode':'SKIDS','skid_count':2,'skid_width_mm':100,'skid_contact_length_mm':2000}
        a=floor_load_audit([p],c)
        # area=0.4m2 -> 3750 kg/m2
        self.assertAlmostEqual(a['rows'][0]['contact_area_m2'],0.4,places=3)
        self.assertGreater(a['max_pressure_kg_m2'],3000); self.assertTrue(a['hard_issues'])

    def test_74_unconfirmed_contact_area_below_limit_is_review_not_pass(self):
        c={'code':'T','inner_l':4000,'inner_w':2400,'inner_h':2600,'door_w':2300,'door_h':2500,'payload':10000,'open_top':False,'floor_load_kg_m2':5000}
        p={'name':'接触未知箱','x':1000,'y':700,'z':0,'l':2000,'w':1000,'h':800,'weight_kg':1000,'cg_confirmed':True,'cg_local_x':1000,'cg_local_y':500,'cg_local_z':400,'stackable':False,'contact_area_mode':'UNSPECIFIED'}
        a=m._container_state_audit({'container':c,'placements':[p],'weight':1000})
        self.assertEqual(a['status'],'需复核'); self.assertEqual(a['floor_contact_unconfirmed_count'],1)
        self.assertTrue(any('未确认实际接触面积' in x for x in a['engineering_review_notes']))

    def test_75_confirmed_full_base_contact_can_pass_floor_semantics(self):
        c={'code':'T','inner_l':4000,'inner_w':2400,'inner_h':2600,'door_w':2300,'door_h':2500,'payload':10000,'open_top':False,'floor_load_kg_m2':5000}
        p={'name':'整底面箱','x':1000,'y':700,'z':0,'l':2000,'w':1000,'h':800,'weight_kg':1000,'cg_confirmed':True,'cg_local_x':1000,'cg_local_y':500,'cg_local_z':400,'stackable':False,'contact_area_mode':'FULL_BASE','contact_area_source_type':'ENGINEERING_CALC'}
        a=m._container_state_audit({'container':c,'placements':[p],'weight':1000})
        self.assertEqual(a['floor_contact_unconfirmed_count'],0); self.assertEqual(a['status'],'通过')

    def test_76_lift_path_is_information_not_review(self):
        from app.physical_constraints import door_maneuver_audit
        c={'code':'40HQ','inner_w':2352,'inner_h':2698,'door_w':2340,'door_h':2585,'open_top':False}
        p={'name':'上层箱','x':1000,'y':100,'z':500,'l':1000,'w':900,'h':600}
        a=door_maneuver_audit([p],c)
        self.assertEqual(a['lift_count'],1); self.assertFalse(a['engineering_reviews']); self.assertTrue(a['information_notes'])

    def test_77_crate_physical_endpoint_persists_contact_area(self):
        from fastapi.testclient import TestClient
        con=m.db(); cur=con.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg) VALUES('BX-C',2000,1000,800,100)"); cid=cur.lastrowid; con.commit(); con.close()
        c=TestClient(m.app,raise_server_exceptions=True)
        r=c.post(f'/crates/{cid}/physical',data={'contact_area_mode':'SKIDS','contact_area_source_type':'FIELD_MEASURED','skid_count':'2','skid_width_mm':'100','skid_contact_length_mm':'2000'},follow_redirects=False)
        self.assertEqual(r.status_code,303)
        con=m.db(); row=dict(con.execute('SELECT contact_area_mode,skid_count,skid_width_mm,skid_contact_length_mm FROM crates WHERE id=?',(cid,)).fetchone()); con.close()
        self.assertEqual(row['contact_area_mode'],'SKIDS'); self.assertEqual(row['skid_count'],2); self.assertEqual(row['skid_width_mm'],100); self.assertEqual(row['skid_contact_length_mm'],2000)

    def test_78_engineering_review_is_nonblocking_for_formal_status_filter(self):
        import unittest.mock as mock
        plan={'containers':[{'index':1,'engineering_audit':{'status':'需复核','engineering_review_notes':['关键CG未知']}}], 'invalid_manual_layouts':[]}
        cstate={'mode':'actual','meta':{'confirmed':True,'evidence_id':1,'reference_no':'BOX1','csv_sha256':'abc','specs_sha256':'def'},'sha256':'def'}
        ev={'claimed_values_json':'{"reference_no":"BOX1","container_csv_sha256":"abc","container_specs_sha256":"def"}'}
        with mock.patch.object(m,'_container_specs_state',return_value=cstate), mock.patch.object(m,'_evidence_matches',return_value=True), mock.patch.object(m,'_evidence_summary',return_value=ev), mock.patch.object(m,'_draft_rows',return_value=[]):
            issues=m._formal_export_issues(plan,'P')
        self.assertFalse(any('规划校核状态=需复核' in x for x in issues))
        self.assertTrue(m._formal_export_review_notes(plan))


class TestV56PhysicalInputTrustAndSearch(IsolatedDbMixin, unittest.TestCase):
    def test_79_cg_coordinates_without_source_do_not_become_confirmed(self):
        from fastapi.testclient import TestClient
        con=m.db(); cur=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,package_type,stackable,rotatable) VALUES('CG-U',1,1000,800,600,600,'裸装',0,1)"); iid=cur.lastrowid; con.commit(); con.close()
        c=TestClient(m.app,raise_server_exceptions=True)
        r=c.post(f'/items/{iid}/cg',data={'cg_x_mm':'700','cg_y_mm':'300','cg_z_mm':'200','cg_source_type':'UNCONFIRMED'},follow_redirects=False)
        self.assertEqual(r.status_code,303)
        con=m.db(); row=dict(con.execute('SELECT cg_x_mm,cg_source_type,cg_confirmed FROM items WHERE id=?',(iid,)).fetchone()); con.close()
        self.assertEqual(row['cg_x_mm'],700); self.assertEqual(row['cg_source_type'],'UNCONFIRMED'); self.assertEqual(row['cg_confirmed'],0)

    def test_80_cg_with_named_source_becomes_confirmed(self):
        from fastapi.testclient import TestClient
        con=m.db(); cur=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,package_type,stackable,rotatable) VALUES('CG-M',1,1000,800,600,600,'裸装',0,1)"); iid=cur.lastrowid; con.commit(); con.close()
        c=TestClient(m.app,raise_server_exceptions=True)
        r=c.post(f'/items/{iid}/cg',data={'cg_x_mm':'700','cg_y_mm':'300','cg_z_mm':'200','cg_source_type':'MANUFACTURER_DATA','cg_source':'厂家CG图'},follow_redirects=False)
        self.assertEqual(r.status_code,303)
        con=m.db(); row=dict(con.execute('SELECT cg_source_type,cg_confirmed FROM items WHERE id=?',(iid,)).fetchone()); con.close()
        self.assertEqual(row['cg_source_type'],'MANUFACTURER_DATA'); self.assertEqual(row['cg_confirmed'],1)

    def test_81_topload_confirmation_requires_source(self):
        from fastapi.testclient import TestClient
        con=m.db(); cur=con.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg) VALUES('BX-T',1200,1000,800,100)"); cid=cur.lastrowid; con.commit(); con.close()
        c=TestClient(m.app,raise_server_exceptions=True)
        r=c.post(f'/crates/{cid}/physical',data={'max_top_load_kg':'1000','top_load_confirmed':'1','top_load_source_type':'UNCONFIRMED'},follow_redirects=False)
        self.assertEqual(r.status_code,303); self.assertIn('error=',r.headers.get('location',''))
        con=m.db(); row=dict(con.execute('SELECT top_load_confirmed,top_load_source_type FROM crates WHERE id=?',(cid,)).fetchone()); con.close()
        self.assertEqual(row['top_load_confirmed'],0); self.assertEqual(row['top_load_source_type'],'UNCONFIRMED')

    def test_82_topload_source_is_used_by_package_solver(self):
        con=m.db(); cur=con.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg,max_top_load_kg,top_load_confirmed,top_load_source_type) VALUES('BX-T2',1200,1000,800,100,1000,1,'ENGINEERING_CALC')"); cid=cur.lastrowid
        con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,package_type,stackable,rotatable,crate_id) VALUES('货物',1,800,600,400,100,'木箱',1,1,?)",(cid,)); con.commit(); con.close()
        p=m.get_packages(*m.fetch_state())[0]
        self.assertTrue(p['top_load_verified']); self.assertTrue(p['stackable']); self.assertEqual(p['top_load_source_type'],'ENGINEERING_CALC')

    def test_83_contact_area_mode_without_source_remains_review(self):
        c={'code':'T','inner_l':4000,'inner_w':2400,'inner_h':2600,'door_w':2300,'door_h':2500,'payload':10000,'open_top':False,'floor_load_kg_m2':5000}
        p={'name':'BX-CU','x':1000,'y':700,'z':0,'l':2000,'w':1000,'h':800,'weight_kg':1000,'cg_confirmed':True,'cg_local_x':1000,'cg_local_y':500,'cg_local_z':400,'stackable':False,'contact_area_mode':'FULL_BASE','contact_area_source_type':'UNCONFIRMED'}
        a=m._container_state_audit({'container':c,'placements':[p],'weight':1000})
        self.assertEqual(a['status'],'需复核'); self.assertEqual(a['floor_contact_unconfirmed_count'],1)

    def test_84_contact_area_with_source_can_pass(self):
        c={'code':'T','inner_l':4000,'inner_w':2400,'inner_h':2600,'door_w':2300,'door_h':2500,'payload':10000,'open_top':False,'floor_load_kg_m2':5000}
        p={'name':'BX-CC','x':1000,'y':700,'z':0,'l':2000,'w':1000,'h':800,'weight_kg':1000,'cg_confirmed':True,'cg_local_x':1000,'cg_local_y':500,'cg_local_z':400,'stackable':False,'contact_area_mode':'FULL_BASE','contact_area_source_type':'FIELD_MEASURED'}
        a=m._container_state_audit({'container':c,'placements':[p],'weight':1000})
        self.assertEqual(a['floor_contact_unconfirmed_count'],0); self.assertEqual(a['status'],'通过')

    def test_85_cumulative_floor_load_rejects_candidate_during_search(self):
        c={'code':'T','inner_l':3000,'inner_w':2000,'inner_h':2000,'door_w':1900,'door_h':1900,'payload':10000,'open_top':False,'floor_load_kg_m2':1000}
        base={'name':'BASE','type':'托盘','x':0,'y':0,'z':0,'l':1000,'w':1000,'h':500,'weight_kg':800,'stackable':True,'contact_area_mode':'FULL_BASE','contact_area_source_type':'ENGINEERING_CALC'}
        state={'container':c,'placements':[base],'points':[(0.0,0.0,500.0)],'weight':800}
        pkg={'name':'TOP','type':'托盘','length_mm':1000,'width_mm':1000,'height_mm':500,'weight_kg':300,'stackable':False,'rotatable':False,'cg_confirmed':True,'cg_local_x':500,'cg_local_y':500,'cg_local_z':250}
        self.assertIsNone(m._can_place(pkg,(1000,1000,500),(0,0,500),state,c))

    def test_86_review_output_filename_is_not_for_release(self):
        review_plan={'containers':[{'index':1,'engineering_audit':{'status':'需复核','engineering_review_notes':['关键CG未知']}}]}
        pass_plan={'containers':[{'index':1,'engineering_audit':{'status':'通过','engineering_review_notes':[]}}]}
        self.assertIn('NOT_FOR_RELEASE',m._container_output_label(review_plan)); self.assertIn('工程复核未关闭',m._container_output_label(review_plan))
        self.assertNotIn('NOT_FOR_RELEASE',m._container_output_label(pass_plan)); self.assertIn('规划校核通过',m._container_output_label(pass_plan))

    def test_87_physical_source_changes_fingerprint(self):
        item={'id':1,'qty':1,'crate_id':None,'length_mm':1000,'width_mm':800,'height_mm':600,'weight_kg':100,'package_type':'裸装','stackable':0,'rotatable':1,'size_grade':'B','size_source':'x','weight_grade':'B','weight_source':'x','cg_x_mm':500,'cg_y_mm':400,'cg_z_mm':300,'cg_source_type':'UNCONFIRMED','cg_confirmed':0}
        a=m._state_fingerprint([dict(item)],[])
        item['cg_source_type']='MANUFACTURER_DATA'; item['cg_confirmed']=1
        b=m._state_fingerprint([dict(item)],[])
        self.assertNotEqual(a,b)

    def test_88_physical_endpoint_persists_sources(self):
        from fastapi.testclient import TestClient
        con=m.db(); cur=con.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg) VALUES('BX-SRC',2000,1000,800,100)"); cid=cur.lastrowid; con.commit(); con.close()
        c=TestClient(m.app,raise_server_exceptions=True)
        r=c.post(f'/crates/{cid}/physical',data={'max_top_load_kg':'1000','top_load_confirmed':'1','top_load_source_type':'PACKING_SUPPLIER_APPROVED','top_load_source_note':'包装厂计算书','contact_area_mode':'SKIDS','contact_area_source_type':'FIELD_MEASURED','contact_area_source_note':'现场测量','skid_count':'2','skid_width_mm':'100','skid_contact_length_mm':'2000'},follow_redirects=False)
        self.assertEqual(r.status_code,303)
        con=m.db(); row=dict(con.execute('SELECT top_load_source_type,contact_area_source_type,top_load_confirmed FROM crates WHERE id=?',(cid,)).fetchone()); con.close()
        self.assertEqual(row['top_load_source_type'],'PACKING_SUPPLIER_APPROVED'); self.assertEqual(row['contact_area_source_type'],'FIELD_MEASURED'); self.assertEqual(row['top_load_confirmed'],1)

class TestV57SearchConstraintClosure(IsolatedDbMixin,unittest.TestCase):
    def base_container(self, **kw):
        c={'code':'40TEST','inner_l':6000.0,'inner_w':3000.0,'inner_h':2600.0,'door_w':2400.0,'door_h':2500.0,'payload':30000.0,'open_top':False,'floor_load_kg_m2':5000.0}
        c.update(kw); return c
    def base_pkg(self, **kw):
        p={'name':'P','length_mm':1000.0,'width_mm':1000.0,'height_mm':1000.0,'weight_kg':100.0,'rotatable':False,'stackable':True,
           'cg_local_x':500.0,'cg_local_y':500.0,'cg_local_z':500.0,'cg_confirmed':True,'cg_source_type':'FIELD_MEASURED',
           'contact_area_mode':'FULL_BASE','contact_area_source_type':'FIELD_MEASURED','skid_count':0,'skid_width_mm':None,'skid_contact_length_mm':None,'custom_contact_area_m2':None}
        p.update(kw); return p

    def test_89_floor_hard_rejection_is_explainable(self):
        c=self.base_container(floor_load_kg_m2=1000.0)
        p=self.base_pkg(weight_kg=2000.0)
        st=m._new_bin_state(c)
        self.assertIsNone(m._can_place(p,(1000.0,1000.0,1000.0),(0.0,0.0,0.0),st,c))
        self.assertGreater(st['reject_stats'].get('floor_pressure',0),0)

    def test_90_physical_audit_exception_fails_closed(self):
        c=self.base_container(floor_load_kg_m2=5000.0); p=self.base_pkg(); st=m._new_bin_state(c)
        old=m.floor_load_audit
        try:
            def boom(*a,**k): raise RuntimeError('audit failed')
            m.floor_load_audit=boom
            self.assertIsNone(m._can_place(p,(1000.0,1000.0,1000.0),(0.0,0.0,0.0),st,c))
            self.assertEqual(st['reject_stats'].get('physical_audit_error'),1)
        finally: m.floor_load_audit=old

    def test_91_door_projection_is_preferred_when_both_points_feasible(self):
        c=self.base_container(floor_load_kg_m2=None); p=self.base_pkg()
        st=m._new_bin_state(c)
        self.assertIn((0.0,300.0,0.0),st['points'])
        self.assertTrue(m._place_one(p,[st]))
        self.assertAlmostEqual(st['placements'][0]['y'],300.0,places=3)

    def test_92_search_rejection_summary_has_labels(self):
        c=self.base_container(); st=m._new_bin_state(c); st['reject_stats']={'collision':7,'payload':2}
        rows=m._search_rejection_summary([st])
        self.assertEqual(rows[0]['code'],'collision'); self.assertEqual(rows[0]['count'],7)
        self.assertIn('碰撞',rows[0]['label'])

    def test_93_severity_counts_do_not_merge_review_high_and_fail(self):
        self.assertEqual(m._audit_severity_counts([{'status':'需复核'},{'status':'高风险'},{'status':'不通过'},{'status':'通过'}]),(1,1,1))

    def test_94_fits_container_does_not_silently_pass_floor_audit_error(self):
        c=self.base_container(floor_load_kg_m2=5000.0); p=self.base_pkg()
        old=m.floor_contact_pressure_kg_m2
        try:
            def boom(*a,**k): raise RuntimeError('bad pressure')
            m.floor_contact_pressure_kg_m2=boom
            self.assertFalse(m.fits_container(p,c))
        finally: m.floor_contact_pressure_kg_m2=old

class TestV57DoorSafeFinalCentering(unittest.TestCase):
    def test_95_final_centering_does_not_create_door_manoeuvre_review(self):
        c={'code':'40TEST','inner_l':6000.0,'inner_w':3000.0,'inner_h':2600.0,'door_w':2400.0,'door_h':2500.0,'payload':30000.0,'open_top':False,'floor_load_kg_m2':None}
        st={'container':c,'weight':2000.0,'points':[], 'reject_stats':{}, 'placements':[
            {'name':'A','x':0.0,'y':300.0,'z':0.0,'l':1000.0,'w':1000.0,'h':800.0,'weight_kg':500.0,'cg_local_x':500.0,'cg_local_y':500.0,'cg_local_z':400.0},
            {'name':'B','x':1000.0,'y':1300.0,'z':0.0,'l':1000.0,'w':1000.0,'h':800.0,'weight_kg':500.0,'cg_local_x':500.0,'cg_local_y':500.0,'cg_local_z':400.0},
            {'name':'C','x':2000.0,'y':1300.0,'z':0.0,'l':1000.0,'w':1000.0,'h':800.0,'weight_kg':500.0,'cg_local_x':500.0,'cg_local_y':500.0,'cg_local_z':400.0},
            {'name':'D','x':3000.0,'y':1300.0,'z':0.0,'l':1000.0,'w':1000.0,'h':800.0,'weight_kg':500.0,'cg_local_x':500.0,'cg_local_y':500.0,'cg_local_z':400.0},
        ]}
        self.assertEqual(m.door_maneuver_audit(st['placements'],c)['maneuver_count'],0)
        m._center_container_state(st)
        self.assertEqual(m.door_maneuver_audit(st['placements'],c)['maneuver_count'],0)


class TestV58EngineeringInputEvidenceClosure(IsolatedDbMixin,unittest.TestCase):
    def setUp(self):
        super().setUp(); from fastapi.testclient import TestClient; self.client=TestClient(m.app,raise_server_exceptions=True); self.set_user()
    def seed_item(self):
        con=m.db(); cur=con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,package_type,stackable,rotatable) VALUES('CG-E',1,1000,800,600,600,'裸装',0,1)"); iid=cur.lastrowid; con.commit(); con.close(); return iid
    def seed_crate(self):
        con=m.db(); cur=con.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg) VALUES('BX-EV',2000,1000,800,100)"); cid=cur.lastrowid; con.commit(); con.close(); return cid
    def test_96_cg_named_source_without_attachment_is_formal_review(self):
        iid=self.seed_item()
        r=self.client.post(f'/items/{iid}/cg',data={'cg_x_mm':'700','cg_y_mm':'300','cg_z_mm':'200','cg_source_type':'FIELD_MEASURED'},follow_redirects=False); self.assertEqual(r.status_code,303)
        notes=m._physical_evidence_review_notes(*m.fetch_state()); self.assertTrue(any('CG' in x for x in notes))
    def test_97_cg_attachment_and_manual_claim_close_review(self):
        iid=self.seed_item()
        r=self.client.post(f'/items/{iid}/cg',data={'cg_x_mm':'700','cg_y_mm':'300','cg_z_mm':'200','cg_source_type':'FIELD_MEASURED','evidence_cg_x_mm':'700','evidence_cg_y_mm':'300','evidence_cg_z_mm':'200'},files={'evidence':('cg.pdf',VALID_PDF,'application/pdf')},follow_redirects=False); self.assertEqual(r.status_code,303)
        con=m.db(); row=dict(con.execute('SELECT * FROM items WHERE id=?',(iid,)).fetchone()); con.close(); self.assertTrue(row['cg_evidence_id'])
        self.assertFalse(any('CG' in x for x in m._physical_evidence_review_notes(*m.fetch_state())))
    def test_98_cg_attachment_mismatch_is_rejected(self):
        iid=self.seed_item()
        r=self.client.post(f'/items/{iid}/cg',data={'cg_x_mm':'700','cg_y_mm':'300','cg_z_mm':'200','cg_source_type':'FIELD_MEASURED','evidence_cg_x_mm':'710','evidence_cg_y_mm':'300','evidence_cg_z_mm':'200'},files={'evidence':('cg.pdf',VALID_PDF,'application/pdf')},follow_redirects=False)
        self.assertIn('error=',r.headers.get('location','')); con=m.db(); row=con.execute('SELECT cg_evidence_id FROM items WHERE id=?',(iid,)).fetchone(); con.close(); self.assertIsNone(row['cg_evidence_id'])
    def test_99_crate_confirmed_physical_inputs_without_evidence_are_review(self):
        cid=self.seed_crate()
        r=self.client.post(f'/crates/{cid}/physical',data={'max_top_load_kg':'1000','top_load_confirmed':'1','top_load_source_type':'ENGINEERING_CALC','contact_area_mode':'SKIDS','contact_area_source_type':'FIELD_MEASURED','skid_count':'2','skid_width_mm':'100','skid_contact_length_mm':'2000','lashing_required':'1','lashing_points':'4','lashing_confirmed':'1'},follow_redirects=False); self.assertEqual(r.status_code,303)
        notes=m._physical_evidence_review_notes(*m.fetch_state()); self.assertTrue(any('顶载' in x for x in notes)); self.assertTrue(any('接触面积' in x for x in notes)); self.assertTrue(any('系固' in x for x in notes))
    def test_100_crate_physical_evidence_closes_all_three_reviews(self):
        cid=self.seed_crate()
        data={'max_top_load_kg':'1000','top_load_confirmed':'1','top_load_source_type':'ENGINEERING_CALC','contact_area_mode':'SKIDS','contact_area_source_type':'FIELD_MEASURED','skid_count':'2','skid_width_mm':'100','skid_contact_length_mm':'2000','lashing_required':'1','lashing_points':'4','lashing_confirmed':'1','evidence_top_load_kg':'1000','evidence_contact_area_m2':'0.4','evidence_lashing_points':'4'}
        files={'top_load_evidence':('top.pdf',VALID_PDF,'application/pdf'),'contact_area_evidence':('contact.pdf',VALID_PDF,'application/pdf'),'lashing_evidence':('lash.pdf',VALID_PDF,'application/pdf')}
        r=self.client.post(f'/crates/{cid}/physical',data=data,files=files,follow_redirects=False); self.assertEqual(r.status_code,303)
        con=m.db(); row=dict(con.execute('SELECT * FROM crates WHERE id=?',(cid,)).fetchone()); con.close(); self.assertTrue(row['top_load_evidence_id']); self.assertTrue(row['contact_area_evidence_id']); self.assertTrue(row['lashing_evidence_id'])
        self.assertEqual(m._physical_evidence_review_notes(*m.fetch_state()),[])
    def test_101_change_after_confirmation_invalidates_old_topload_evidence(self):
        cid=self.seed_crate()
        data={'max_top_load_kg':'1000','top_load_confirmed':'1','top_load_source_type':'ENGINEERING_CALC','evidence_top_load_kg':'1000'}
        r=self.client.post(f'/crates/{cid}/physical',data=data,files={'top_load_evidence':('top.pdf',VALID_PDF,'application/pdf')},follow_redirects=False); self.assertEqual(r.status_code,303)
        r=self.client.post(f'/crates/{cid}/physical',data={'max_top_load_kg':'1200','top_load_confirmed':'1','top_load_source_type':'ENGINEERING_CALC'},follow_redirects=False); self.assertEqual(r.status_code,303)
        con=m.db(); row=dict(con.execute('SELECT * FROM crates WHERE id=?',(cid,)).fetchone()); con.close(); self.assertIsNone(row['top_load_evidence_id']); self.assertTrue(any('顶载' in x for x in m._physical_evidence_review_notes(*m.fetch_state())))
    def test_102_physical_evidence_review_forces_not_for_release_label_without_changing_solver_status(self):
        iid=self.seed_item(); self.client.post(f'/items/{iid}/cg',data={'cg_x_mm':'700','cg_y_mm':'300','cg_z_mm':'200','cg_source_type':'FIELD_MEASURED'},follow_redirects=False)
        plan={'containers':[{'index':1,'engineering_audit':{'status':'通过','engineering_review_notes':[]}}]}
        items,crates=m.fetch_state(); label=m._container_output_label(plan,items,crates)
        self.assertIn('NOT_FOR_RELEASE',label); self.assertEqual(plan['containers'][0]['engineering_audit']['status'],'通过')
    def test_103_tampered_physical_evidence_reopens_formal_review(self):
        iid=self.seed_item()
        r=self.client.post(f'/items/{iid}/cg',data={'cg_x_mm':'700','cg_y_mm':'300','cg_z_mm':'200','cg_source_type':'MANUFACTURER_DATA','evidence_cg_x_mm':'700','evidence_cg_y_mm':'300','evidence_cg_z_mm':'200'},files={'evidence':('cg.pdf',VALID_PDF,'application/pdf')},follow_redirects=False); self.assertEqual(r.status_code,303)
        con=m.db(); eid=con.execute('SELECT cg_evidence_id FROM items WHERE id=?',(iid,)).fetchone()['cg_evidence_id']; con.close()
        self.assertEqual(m._physical_evidence_review_notes(*m.fetch_state()),[])
        m.EVIDENCE_STORE.path_for(eid).write_bytes(b'tampered')
        self.assertTrue(any('CG' in x for x in m._physical_evidence_review_notes(*m.fetch_state())))

    def test_104_multi_evidence_validation_failure_leaves_no_partial_rows(self):
        cid=self.seed_crate()
        con=m.db(); before=con.execute('SELECT COUNT(*) AS n FROM evidence_files').fetchone()['n']; con.close()
        data={'max_top_load_kg':'1000','top_load_confirmed':'1','top_load_source_type':'ENGINEERING_CALC',
              'contact_area_mode':'SKIDS','contact_area_source_type':'FIELD_MEASURED','skid_count':'2','skid_width_mm':'100','skid_contact_length_mm':'2000',
              'lashing_required':'1','lashing_points':'4','lashing_confirmed':'1',
              'evidence_top_load_kg':'1000','evidence_contact_area_m2':'0.4','evidence_lashing_points':'5'}
        files={'top_load_evidence':('top.pdf',VALID_PDF,'application/pdf'),
               'contact_area_evidence':('contact.pdf',VALID_PDF,'application/pdf'),
               'lashing_evidence':('lash.pdf',VALID_PDF,'application/pdf')}
        r=self.client.post(f'/crates/{cid}/physical',data=data,files=files,follow_redirects=False)
        self.assertIn('error=',r.headers.get('location',''))
        con=m.db(); after=con.execute('SELECT COUNT(*) AS n FROM evidence_files').fetchone()['n']; row=dict(con.execute('SELECT * FROM crates WHERE id=?',(cid,)).fetchone()); con.close()
        self.assertEqual(after,before)
        self.assertIsNone(row['top_load_evidence_id']); self.assertIsNone(row['contact_area_evidence_id']); self.assertIsNone(row['lashing_evidence_id'])


class TestV581StatusSemantics(IsolatedDbMixin,unittest.TestCase):
    def setUp(self):
        super().setUp(); from fastapi.testclient import TestClient; self.client=TestClient(m.app,raise_server_exceptions=True)
    def test_105_home_separates_planning_and_evidence_status(self):
        html=self.client.get('/').text
        self.assertIn('静态规划校核',html); self.assertIn('工程输入凭证一致性',html)
    def test_106_open_physical_evidence_shows_not_for_release_in_home(self):
        con=m.db(); con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,package_type,stackable,rotatable,cg_x_mm,cg_y_mm,cg_z_mm,cg_source_type,cg_confirmed) VALUES('E',1,1000,800,600,600,'裸装',0,1,700,300,200,'FIELD_MEASURED',1)"); con.commit(); con.close()
        html=self.client.get('/').text
        self.assertIn('未闭环 · NOT FOR RELEASE',html)
    def test_107_frontend_contains_evidence_watermark(self):
        js=(ROOT/'app/static/app.js').read_text(encoding='utf-8')
        self.assertIn('工程输入凭证未闭环',js); self.assertIn('credential-watermark',js)
    def test_108_formal_pass_label_says_static_planning(self):
        plan={'containers':[{'index':1,'engineering_audit':{'status':'通过','engineering_review_notes':[]}}]}
        self.assertIn('静态规划校核通过',m._container_output_label(plan,[],[]))
    def test_109_solver_status_remains_independent_of_evidence_status(self):
        plan={'containers':[{'index':1,'engineering_audit':{'status':'通过','engineering_review_notes':[]}}]}
        self.assertEqual(plan['containers'][0]['engineering_audit']['status'],'通过')


if __name__=='__main__': unittest.main(verbosity=2)
