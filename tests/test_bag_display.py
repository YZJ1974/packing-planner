import unittest,copy
from xml.etree import ElementTree as ET
from app.bag_display import patches,svg_sack
from app.svg_exports import render_svg

class BagDisplayTests(unittest.TestCase):
    def test_representative_patches_are_bounded_and_do_not_mutate_volume(self):
        zone=dict(x=100,y=200,z=300,l=12000,w=2352,h=2698);before=copy.deepcopy(zone);mesh=patches([zone])
        self.assertTrue(0<len(mesh)<=360);self.assertEqual(zone,before)
        for p in mesh:
            for q in p['corners']:
                for i,(a,d) in enumerate(zip(('x','y','z'),('l','w','h'))):self.assertTrue(zone[a]-1e-6<=q[i]<=zone[a]+zone[d]+1e-6)
        self.assertTrue(any(all(q[2]==zone['z']+zone['h'] for q in p['corners']) for p in mesh))
    def test_thin_unverified_slivers_are_not_depicted_as_real_bags(self):
        self.assertEqual(patches([dict(x=0,y=0,z=0,l=12000,w=100,h=2698)]),[])
    def test_sack_has_curved_body_and_sealed_mouth(self):
        root=ET.Element('g');svg_sack(root,[(0,0),(100,0),(100,60),(0,60)],lambda p,t,a:ET.SubElement(p,t,a))
        self.assertEqual(len(root.findall('line')),3);self.assertIn('C',root.find('path').get('d'));self.assertIn('Q',root.find('path').get('d'));self.assertEqual(root.find('path').get('data-bag-glyph'),'representative-only')
    def test_export_preserves_exact_quantity_and_labels_representative_art(self):
        zone=dict(x=0,y=0,z=0,l=12032,w=2352,h=2698)
        box=dict(index=1,container_code='40HQ',inner_l=12032,inner_w=2352,inner_h=2698,placements=[],flexible_fill=[dict(quantity=271)],bag_display_patches=patches([zone]))
        before=copy.deepcopy(box)
        for view in ('perspective','top','front','side'):
            root=ET.fromstring(render_svg(box,view));text=''.join(root.itertext());self.assertIn('271包',text);self.assertIn('图中袋形数非实包数',text);self.assertTrue(any(e.get('data-bag-glyph')=='representative-only' for e in root.iter()))
        self.assertEqual(box,before)
