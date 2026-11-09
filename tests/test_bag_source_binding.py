import copy,json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from test_v58_regression import IsolatedDbMixin,m
from app import bag_top_policy as bp
from app.shipment_source import fingerprint

class SourceBoundBagPolicyTests(IsolatedDbMixin,TestCase):
    def q(self):return dict(name='纯水机',asu_key='item:1',length_mm=5700,width_mm=1600,height_mm=1600,type='裸装',weight_kg=2000)
    def policy(self):return dict(schema=2,enabled=True,source='USER_PROJECT_DECLARATION',max_bag_density_kg_m3=56,shipment_sha256='a'*64,shipment_fingerprint='b'*64,approved_package_signatures=[bp.signature(self.q(),stable=True)])
    def test_same_source_reimport_can_change_database_ids(self):
        p=self.policy();q=self.q();self.assertTrue(bp.approved(q,p));q['asu_key']='item:999';self.assertTrue(bp.approved(q,p))
        q['length_mm'],q['width_mm']=q['width_mm'],q['length_mm'];self.assertTrue(bp.approved(q,p))
    def test_standing_orientation_and_weight_or_name_changes_revoke_permission(self):
        p=self.policy()
        for change in (dict(height_mm=5700,length_mm=1600),dict(weight_kg=2100),dict(name='其他设备')):
            self.assertFalse(bp.approved(dict(self.q(),**change),p))
    def test_other_source_or_material_fingerprint_rejects_policy(self):
        data=m.DB_PATH.parent;p=self.policy();(data/'bag_top_policy.json').write_text(json.dumps(p))
        source=dict(status='verified',sha256=p['shipment_sha256'],fingerprint=p['shipment_fingerprint'],issues=[])
        for key,bad in [('sha256','c'*64),('fingerprint','c'*64),('status','mismatch')]:
            changed=dict(source,**{key:bad});(data/'shipment_source.json').write_text(json.dumps(changed));self.assertEqual(bp.load(data),{})
        (data/'shipment_source.json').write_text(json.dumps(source));self.assertEqual(bp.load(data)['schema'],2)
    def test_actual_material_change_disables_even_unchanged_source_manifest(self):
        p=self.policy()
        with patch.object(m,'_load_bag_top_policy',return_value=p),patch.object(m,'fetch_state',return_value=([],[])),patch.object(m,'_shipment_source_state',return_value=dict(status='mismatch',issues=['changed'],fingerprint=p['shipment_fingerprint'])):
            self.assertEqual(m._bag_fill_policy(),{})
    def test_zero_matching_permission_has_visible_reason(self):
        bag=dict(name='珠光砂',flexible_unit_volume_m3=.125)
        with patch.object(m,'get_packages',return_value=[self.q(),bag]),patch.object(m,'_bag_fill_policy',return_value={}):
            status=m._bag_permission_status([],[])
        self.assertTrue(status['stale']);self.assertEqual(status['approved'],0);self.assertIn('顶部空间未计入',status['note'])
