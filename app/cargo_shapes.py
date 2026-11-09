"""Display meshes only; conservative packing envelopes remain authoritative."""
import math,re

def upright_drum(package):
    return str(package or '').strip().lower() in ('桶','铁桶','钢桶','塑料桶','桶装','铁桶装','钢桶装','tambor','barrel','steel drum')

def cylinder_axis(q):
    if '木箱' in str(q.get('type','')):return None
    upright=q.get('display_shape')=='upright_cylinder' or upright_drum(q.get('type'))
    explicit=q.get('display_shape')=='horizontal_cylinder'
    named=bool(re.search(r'吸附器|adsorber',str(q.get('name','')),re.I))
    if not (upright or explicit or named):return None
    l,w,h=[float(q.get(k,0)) for k in ('l','w','h')]
    if min(l,w,h)<=0:return None
    if upright:return 'z' if math.isclose(l,w,rel_tol=1e-9,abs_tol=1e-6) else None
    if math.isclose(w,h,rel_tol=1e-9,abs_tol=1e-6):return 'x'
    if math.isclose(l,h,rel_tol=1e-9,abs_tol=1e-6):return 'y'
    return None

def cylinder_faces(q,segments=48):
    axis=cylinder_axis(q)
    if axis is None:return []
    x,y,z,l,w,h=[float(q[k]) for k in ('x','y','z','l','w','h')]
    rings=[]
    for end in (0,1):
        ring=[]
        for i in range(segments):
            a=2*math.pi*i/segments
            if axis=='x':point=(x+l*end,y+w/2+w/2*math.cos(a),z+h/2+h/2*math.sin(a))
            elif axis=='y':point=(x+l/2+l/2*math.cos(a),y+w*end,z+h/2+h/2*math.sin(a))
            else:point=(x+l/2+l/2*math.cos(a),y+w/2+w/2*math.sin(a),z+h*end)
            ring.append(point)
        rings.append(ring)
    return [rings[0],list(reversed(rings[1]))]+[[rings[0][i],rings[0][(i+1)%segments],rings[1][(i+1)%segments],rings[1][i]] for i in range(segments)]
