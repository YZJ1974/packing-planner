import copy,unittest
from xml.etree import ElementTree as ET
from test_v58_regression import m,IsolatedDbMixin
from app.cargo_shapes import cylinder_axis,cylinder_faces
from app.svg_exports import render_svg

class CylinderDisplayTests(IsolatedDbMixin,unittest.TestCase):
    def cargo(self,**kw):
        q=dict(x=100,y=100,z=0,l=3000,w=1500,h=1500,name='吸附器1',type='裸装',seq=17,color='#7c3aed',weight_kg=1200);q.update(kw);return q
    def test_existing_adsorber_and_horizontal_rotation(self):
        self.assertEqual(cylinder_axis(self.cargo()),'x');self.assertEqual(cylinder_axis(self.cargo(l=1500,w=3000)),'y')
        self.assertIsNone(cylinder_axis(self.cargo(type='木箱')));self.assertIsNone(cylinder_axis(self.cargo(w=1600)))
        self.assertIsNone(cylinder_axis(self.cargo(name='普通设备')))
    def test_mesh_bounds_and_inputs_unchanged(self):
        for q in (self.cargo(),self.cargo(l=1500,w=3000)):
            before=copy.deepcopy(q);faces=cylinder_faces(q);self.assertEqual(len(faces),50);self.assertEqual(q,before)
            for axis,dim in zip(('x','y','z'),('l','w','h')):
                i=('x','y','z').index(axis);vals=[p[i] for face in faces for p in face]
                self.assertAlmostEqual(min(vals),q[axis]);self.assertAlmostEqual(max(vals),q[axis]+q[dim])
    def test_four_view_exports_have_round_mesh(self):
        q=self.cargo();box=dict(index=1,container_code='40HQ',inner_l=12032,inner_w=2352,inner_h=2698,placements=[q])
        for view in ('perspective','top','front','side'):
            root=ET.fromstring(render_svg(box,view));groups=[e for e in root.iter() if e.get('data-shape')=='horizontal-cylinder'];self.assertEqual(len(groups),1);self.assertEqual(len(groups[0]),50)
    def test_plan_metadata_changes_no_weight_or_dimensions(self):
        c=m.DEFAULT_CONTAINERS[2];q=self.cargo();st=m._new_bin_state(c);st['placements']=[q];st['weight']=1200
        p=m._decorate_plan([st],'QA','QA');r=p['containers'][0]['placements'][0]
        self.assertEqual(r['display_shape'],'horizontal_cylinder');self.assertEqual(r['cylinder_axis'],'x')
        self.assertEqual((r['l'],r['w'],r['h'],r['weight_kg']),(3000,1500,1500,1200))
