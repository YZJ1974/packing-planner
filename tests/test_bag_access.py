import unittest, copy
from unittest.mock import patch
from test_v58_regression import m, IsolatedDbMixin
from app.bag_access import eligible_zones
from app.bag_display import patches
from app import flexible_fill


class BagAccessTests(IsolatedDbMixin, unittest.TestCase):
    def test_narrow_side_void_and_equipment_top_are_excluded(self):
        b=dict(inner_l=12000,inner_w=2352,inner_h=2698,placements=[dict(x=100,y=100,z=0,l=11800,w=1900,h=2200)])
        self.assertEqual(eligible_zones(b),[])

    def test_only_continuous_full_width_floor_at_rear(self):
        b=dict(inner_l=12000,inner_w=2352,inner_h=2698,placements=[dict(x=100,y=100,z=0,l=7000,w=1900,h=2200)])
        self.assertEqual(eligible_zones(b),[(7100.,0.,0.,4900.,2352.,2698.)])

    def test_narrow_door_is_not_treated_as_accessible(self):
        self.assertEqual(eligible_zones(dict(inner_l=12000,inner_w=2352,inner_h=2698,door_w=700)),[])

    def test_display_budget_covers_all_zones(self):
        zones=[dict(x=i*1000,y=0,z=0,l=900,w=2000,h=2000) for i in range(50)]
        mesh=patches(zones)
        self.assertLessEqual(len(mesh),360)
        self.assertEqual({p['zone_index'] for p in mesh},set(range(50)))

    def test_inaccessible_void_adds_container_and_preserves_count_mass(self):
        c=dict(code='QA',inner_l=3000,inner_w=2000,inner_h=2000,door_w=2000,door_h=2000,payload=10000,open_top=False)
        equipment=dict(name='equipment',length_mm=2800,width_mm=1200,height_mm=1000,weight_kg=100,type='裸装',rotatable=False,stackable=False)
        bags=[dict(name='bag-'+str(i),length_mm=560,width_mm=560,height_mm=800,weight_kg=7,type='编织袋',flexible_unit_volume_m3=.125) for i in range(100)]
        plan=flexible_fill.pack(m,[equipment]+bags,c)
        allocations=[a for b in plan['containers'] for a in b.get('flexible_fill',[])]
        self.assertEqual(sum(a['quantity'] for a in allocations),100)
        self.assertEqual(sum(a['weight_kg'] for a in allocations),700)
        for b in plan['containers']:
            if b['placements']:self.assertNotIn('flexible_fill',b)
        self.assertGreaterEqual(plan['count'],3)
        self.assertTrue(m._formal_export_issues(plan,plan['code']))

    def test_default_import_replaces_old_data_and_backs_up(self):
        row=dict(name='new shipment',qty=1,length_mm=1000,width_mm=500,height_mm=500,weight_kg=50,package_type='裸装',stackable=0,rotatable=1)
        m.insert_imported_shipping_rows([dict(row,name='old cable')],mode='append')
        before={p.name for p in (m.DATA_DIR/'import_backups').glob('*.sqlite3')} if (m.DATA_DIR/'import_backups').exists() else set()
        m.insert_imported_shipping_rows([row])
        items,crates=m.fetch_state();self.assertEqual([i['name'] for i in items],['new shipment'])
        self.assertTrue({p.name for p in (m.DATA_DIR/'import_backups').glob('*.sqlite3')}-before)
        m.insert_imported_shipping_rows([dict(row,name='explicit extra')],mode='append')
        self.assertEqual(len(m.fetch_state()[0]),2)

    def test_empty_replace_does_not_erase_current_data(self):
        row=dict(name='retained',qty=1,length_mm=1000,width_mm=500,height_mm=500,weight_kg=50,package_type='裸装',stackable=0,rotatable=1)
        m.insert_imported_shipping_rows([row],mode='append')
        with self.assertRaises(ValueError):m.insert_imported_shipping_rows([])
        self.assertEqual(m.fetch_state()[0][0]['name'],'retained')
