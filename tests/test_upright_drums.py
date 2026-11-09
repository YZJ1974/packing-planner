import unittest,copy
from test_v58_regression import m
from app.cargo_shapes import cylinder_axis,cylinder_faces
from app.svg_exports import render_svg
from xml.etree import ElementTree as ET

class UprightDrumTests(unittest.TestCase):
    def drum(self,**kw):
        q=dict(x=100,y=200,z=0,l=600,w=600,h=1000,name='分子筛',type='铁桶',weight_kg=150,seq=1,color='#059669');q.update(kw);return q
    def test_drum_stands_even_if_all_three_dimensions_equal(self):
        self.assertEqual(cylinder_axis(self.drum()),'z');self.assertEqual(cylinder_axis(self.drum(l=1100,w=1100,h=1100)),'z')
        self.assertIsNone(cylinder_axis(self.drum(l=1200)))
        self.assertIsNone(cylinder_axis(self.drum(type='木箱')))
        self.assertIsNone(cylinder_axis(self.drum(name='电缆盘',type='木滚轴')))
    def test_upright_mesh_preserves_bounds_and_mass(self):
        q=self.drum();before=copy.deepcopy(q);faces=cylinder_faces(q);self.assertEqual(len(faces),50);self.assertEqual(q,before)
        for i,(axis,dim) in enumerate(zip(('x','y','z'),('l','w','h'))):
            v=[p[i] for f in faces for p in f];self.assertAlmostEqual(min(v),q[axis]);self.assertAlmostEqual(max(v),q[axis]+q[dim])
        self.assertTrue(all(p[2]==q['z'] for p in faces[0]))
    def test_import_barrel_diameter_times_height_and_separate_weight(self):
        cols=dict(name=0,size=1,qty=2,weight_kg=3,package_type=4)
        rows=[['分子筛/氧化铝','φ600*1000mm,150KG',41,150,'桶']]
        records,skipped,_=m.parse_material_rows(rows,cols,0);self.assertFalse(skipped);r=records[0]
        self.assertEqual((r['length_mm'],r['width_mm'],r['height_mm'],r['qty'],r['weight_kg']),(600,600,1000,41,150))
        self.assertEqual(r['stackable'],0);self.assertIn('圆桶立放',r['size_note'])
    def test_separate_dimension_columns_are_not_reinterpreted(self):
        r,_,_=m.parse_material_rows([['分子筛1',1100,1100,1100,650,'铁桶']],dict(name=0,length_mm=1,width_mm=2,height_mm=3,weight_kg=4,package_type=5),0)
        self.assertEqual((r[0]['length_mm'],r[0]['height_mm'],r[0]['weight_kg']),(1100,1100,650))
    def test_all_exports_use_upright_circular_mesh(self):
        q=self.drum(display_shape='upright_cylinder',cylinder_axis='z');box=dict(index=1,container_code='40GP',inner_l=12032,inner_w=2352,inner_h=2393,placements=[q])
        for view in ('perspective','top','front','side'):
            root=ET.fromstring(render_svg(box,view));self.assertTrue(any(e.get('data-shape')=='upright-cylinder' for e in root.iter()))
