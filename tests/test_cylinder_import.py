import unittest
from test_v58_regression import m

class CylinderImportTests(unittest.TestCase):
    def test_horizontal_diameter_and_axis_do_not_consume_weight(self):
        for value in ('φ2000*5320mm, 3133KG','Φ2000×5320 mm; 3133 kg por unidad','⌀2000＊5320 mm','ø2000 x 5320mm'):
            with self.subTest(value=value):self.assertEqual(m.parse_size(value),(5320,2000,2000))
    def test_cylinder_units_and_precision(self):
        self.assertEqual(m.parse_size('φ200cm×5.32m'),(5320,2000,2000))
        self.assertEqual(m.parse_size('φ2000.125×5320.375mm'),(5320.375,2000.125,2000.125))
    def test_two_plain_numbers_and_weight_are_not_three_dimensions(self):
        self.assertIsNone(m.parse_size('2000×5320 mm, 3133kg'))
        self.assertIsNone(m.parse_size('钢管 φ219×6 mm, 3133kg'))
        self.assertEqual(m.parse_size('3200×1800×1600 mm, 3133 kg'),(3200,1800,1600))
    def test_import_uses_separate_mass_and_marks_horizontal_envelope(self):
        rows=[['Cylindrical equipment','φ2000*5320mm, 3133KG',3133,1,'裸装']]
        records,skipped,_=m.parse_material_rows(rows,dict(name=0,size=1,weight_kg=2,qty=3,package_type=4),0)
        self.assertFalse(skipped);self.assertEqual(len(records),1);r=records[0]
        self.assertEqual((r['length_mm'],r['width_mm'],r['height_mm'],r['weight_kg']),(5320,2000,2000,3133))
        self.assertEqual(r['stackable'],0);self.assertIn('圆柱卧放',r['size_note']);self.assertIn('未加支座',r['size_note'])
    def test_missing_mass_not_invented_from_size_text(self):
        records,skipped,_=m.parse_material_rows([['Cylinder','φ2000×5320mm, 3133KG',None]],dict(name=0,size=1,weight_kg=2),0)
        self.assertEqual(records,[]);self.assertTrue(skipped)
    def test_packaged_cylinder_keeps_packaging_stack_setting(self):
        records,_,_=m.parse_material_rows([['Cylinder','φ2000×5320mm',3133,'木箱',1]],dict(name=0,size=1,weight_kg=2,package_type=3,stackable=4),0)
        self.assertEqual(records[0]['stackable'],1)
