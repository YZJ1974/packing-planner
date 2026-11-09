import copy,unittest
import test_trucks as base
m,t=base.m,base.t

class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.case=base.TruckTests('test_templates_have_no_invented_payload');self.case.setUp()
    def tearDown(self):self.case.tearDown()
    def truck(self,**kw):
        row=t.defaults()['vehicles'][0];row.update(enabled=True,payload_kg=8000);row.update(kw);return t.as_carrier(row)
    def test_subdecimal_input_changes_cannot_reuse_cache(self):
        self.case.seed(length=2000.01);items,crates=m.fetch_state();fp=m._state_fingerprint(items,crates);m._perf_cache_set('fast',fp,{'old':True})
        for key in ('length_mm','width_mm','height_mm','weight_kg'):
            changed=copy.deepcopy(items);changed[0][key]+=0.00001;new=m._state_fingerprint(changed,crates)
            self.assertNotEqual(fp,new);self.assertIsNone(m._perf_cache_get(m._PERF_CACHE['fast'],new))
    def test_subdecimal_crate_changes_invalidate_cache(self):
        row=dict(id=1,name='C',length_mm=2000.01,width_mm=1000,height_mm=1000,tare_weight_kg=10.01);old=m._state_fingerprint([],[row])
        for key in ('length_mm','width_mm','height_mm','tare_weight_kg'):
            changed=dict(row);changed[key]+=0.01;self.assertNotEqual(old,m._state_fingerprint([],[changed]))
    def test_cargo_alone_above_gross_limit_without_axles(self):
        c=self.truck(max_gross_kg=7000);self.assertEqual(c['payload'],7000)
        self.assertTrue(any('总质量超过' in n for n in t.audit(c,[self.case.cargo(weight=7500)])['hard']))
    def test_independent_empty_mass_limits_solver_without_axles(self):
        self.case.save(empty_mass_kg=3000,max_gross_kg=7000);c=m._active_containers()[0];self.assertEqual(c['payload'],4000)
        self.assertFalse(m.fits_container(dict(length_mm=2000,width_mm=1000,height_mm=1000,weight_kg=4500,rotatable=False),c))
        a=t.audit(c,[self.case.cargo(weight=4500)]);self.assertEqual(a['gross_kg'],7500);self.assertTrue(any('总质量超过' in n for n in a['hard']))
    def test_gross_boundary_and_missing_empty_mass(self):
        a=t.audit(self.truck(empty_mass_kg=3000,max_gross_kg=7000),[self.case.cargo(weight=4000)]);self.assertEqual(a['gross_kg'],7000);self.assertFalse(a['hard'])
        a=t.audit(self.truck(max_gross_kg=7000),[self.case.cargo(weight=4000)]);self.assertIsNone(a['gross_kg']);self.assertTrue(any('缺空载总质量' in n for n in a['review']))
    def test_empty_mass_consistency_and_evidence_hash(self):
        row=self.case.model()['truck_parameters'];old=t.digest(t.parameters(row));row['empty_mass_kg']=7000
        self.assertNotEqual(old,t.digest(t.parameters(t.validate(row))));row['empty_mass_kg']=7001
        with self.assertRaisesRegex(ValueError,'不一致'):t.validate(row)
    def test_invalid_save_preserves_inputs_and_saved_settings(self):
        self.case.save();before=m.TRUCKS.path.read_bytes()
        r=self.case.client.post('/vehicles/save',data=dict(code='TRUCK_6M8',label='RETAIN-ME',reference='MY-REF',enabled='on',body_type='flatbed',length_mm=6999,width_mm=2400,cargo_height_mm=2500,payload_kg=-1),follow_redirects=False)
        self.assertEqual(r.status_code,422)
        for text in ('value="RETAIN-ME"','value="6999"','value="MY-REF"','aria-invalid="true"','附件需重新选择'):self.assertIn(text,r.text)
        self.assertEqual(before,m.TRUCKS.path.read_bytes())
    def test_success_shows_next_step(self):
        r=self.case.save();self.assertIn('继续选择车型和辆数',self.case.client.get(r.headers['location']).text)
    def test_missing_truck_rate_is_not_zero(self):
        self.case.save();self.case.seed();self.case.client.post('/container-selection',data={'mode':'selected','qty:TRUCK_6M8':1});p=self.case.plan()
        self.assertIsNone(p['freight_cost']);self.assertIsNone(p['total_cost']);self.assertIn('未配置',p['cost_status']);self.assertEqual(self.case.client.get('/').status_code,200)
        self.assertEqual(m._plan_cost({'containers':[{'container_code':'40HQ'}]},[],[],{'40HQ':12345})['total_cost'],12345)
    def test_truck_error_vocabulary(self):
        from urllib.parse import unquote
        self.case.save();r=self.case.client.post('/container-selection',data={'mode':'selected'},follow_redirects=False);location=unquote(r.headers['location'])
        self.assertIn('车型',location);self.assertIn('辆数',location);self.assertNotIn('柜型',location)
