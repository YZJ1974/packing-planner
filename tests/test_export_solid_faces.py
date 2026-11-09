import unittest,copy
from xml.etree import ElementTree as ET
from app.svg_exports import render_svg

def hull(points):
    points=sorted(set(points))
    def cross(o,a,b):return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
    lo=[];hi=[]
    for p in points:
        while len(lo)>=2 and cross(lo[-2],lo[-1],p)<=0:lo.pop()
        lo.append(p)
    for p in reversed(points):
        while len(hi)>=2 and cross(hi[-2],hi[-1],p)<=0:hi.pop()
        hi.append(p)
    return lo[:-1]+hi[:-1]
def inside(p,poly):
    signs=[(b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0]) for a,b in zip(poly,poly[1:]+poly[:1])]
    return min(signs)>=-1e-6 or max(signs)<=1e-6

class ExportSolidFaceTests(unittest.TestCase):
    def test_oblique_cuboid_faces_cover_projected_silhouette_without_holes(self):
        for dims in [(3100,2020,2220),(8000,800,700),(1000,1000,1000)]:
            q=dict(seq=1,x=100,y=100,z=0,l=dims[0],w=dims[1],h=dims[2],name='裸装设备',type='裸装',weight_kg=1500,color='#2563eb')
            b=dict(index=1,container_code='40HQ',inner_l=12032,inner_w=2352,inner_h=2698,placements=[q]);before=copy.deepcopy(b)
            root=ET.fromstring(render_svg(b));faces=[[tuple(map(float,p.split(','))) for p in n.get('points').split()] for n in root if n.get('data-cargo-seq')=='1'];self.assertEqual(len(faces),3)
            outline=hull([p for face in faces for p in face]);xs=[p[0] for p in outline];ys=[p[1] for p in outline]
            for i in range(1,60):
                for j in range(1,40):
                    p=(min(xs)+(max(xs)-min(xs))*i/60,min(ys)+(max(ys)-min(ys))*j/40)
                    if inside(p,outline):self.assertTrue(any(inside(p,f) for f in faces),(dims,p))
            self.assertEqual(b,before)

    def test_bag_volume_box_also_has_no_projected_hole(self):
        z=dict(x=100,y=100,z=1000,l=3100,w=2020,h=1500)
        b=dict(index=1,container_code='40HQ',inner_l=12032,inner_w=2352,inner_h=2698,placements=[],flexible_zones=[z]);before=copy.deepcopy(b)
        root=ET.fromstring(render_svg(b));faces=[[tuple(map(float,p.split(','))) for p in n.get('points').split()] for n in root if n.get('data-flexible-zone')=='volume-only']
        self.assertEqual(len(faces),3);outline=hull([p for f in faces for p in f]);xs=[p[0] for p in outline];ys=[p[1] for p in outline]
        for i in range(1,50):
            for j in range(1,30):
                p=(min(xs)+(max(xs)-min(xs))*i/50,min(ys)+(max(ys)-min(ys))*j/30)
                if inside(p,outline):self.assertTrue(any(inside(p,f) for f in faces))
        self.assertEqual(b,before)
