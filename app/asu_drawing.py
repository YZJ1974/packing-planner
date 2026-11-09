"""Three orthographic geometry views from registered inputs, always a review drawing."""
import xml.etree.ElementTree as E
from app.asu_standard import points
def drawing(pkg,data,verified):
    L,W,H=[float(pkg[k]) for k in ('length_mm','width_mm','height_mm')]
    pts=points(data.get('lifting_points_json'),(L,W,H))
    svg=E.Element('svg',{'xmlns':'http://www.w3.org/2000/svg','viewBox':'0 0 1200 1000','font-family':'PingFang SC,Microsoft YaHei,sans-serif'})
    def node(tag,**kw):return E.SubElement(svg,tag,{k.replace('_','-'):str(v) for k,v in kw.items()})
    def text(x,y,s,size=14):r=node('text',x=x,y=y,font_size=size,fill='#193b4d');r.text=str(s)
    node('rect',width=1200,height=1000,fill='white')
    text(35,35,'运输几何复核图 / TRANSPORT GEOMETRY REVIEW',24)
    text(35,65,'DRAFT · 非施工及发运依据 / NOT FOR CONSTRUCTION OR RELEASE',17)
    text(35,92,str(data.get('english_description') or pkg.get('name')))
    text(35,118,f'含包装外形 / ENVELOPE: {L/10:g} × {W/10:g} × {H/10:g} cm; GROSS {pkg["weight_kg"]:g} kg')
    text(35,144,'实际登记点 / REGISTERED POINTS' if verified else '吊点资料未确认 / UNVERIFIED POINT DATA')
    text(35,170,f"Sling angle to horizontal: {data.get('sling_angle_deg') or 'PENDING'} deg; Hook Z: {data.get('hook_height_mm') or 'PENDING'} mm",12)
    cgs=[pkg.get('cg_local_'+a,pkg.get('cg_'+a+'_mm')) for a in 'xyz']
    cgs=[float(v) if v is not None else dim/2 for v,dim in zip(cgs,(L,W,H))]
    for ox,oy,title,axes,wide,high in [(60,205,'侧视 / SIDE X-Z',(0,2),L,H),(60,570,'俯视 / TOP X-Y',(0,1),L,W),(760,570,'端视 / END Y-Z',(1,2),W,H)]:
        width=1040 if oy==205 else 630 if ox==60 else 370
        scale=min(width/max(wide,1),250/max(high,1));bw=wide*scale;bh=high*scale
        text(ox,oy-16,title,18)
        node('rect',x=ox,y=oy,width=bw,height=bh,fill='#eef4f7',stroke='#315364')
        def P(v):return (ox+v[axes[0]]*scale,oy+bh-v[axes[1]]*scale)
        for p in pts:
            x,y=P([p['x'],p['y'],p['z']]);node('circle',cx=x,cy=y,r=5,fill='#db7c19');text(x+8,y-6,f"{p['label']} / {p['capacity_kg']:g}kg",11)
        x,y=P(cgs);node('path',d=f'M{x-8} {y} h16 M{x} {y-8} v16',stroke='#bb253c',stroke_width=2)
        text(x+10,y+14,'CG / '+('登记值' if pkg.get('cg_confirmed') else '几何中心假设'),11)
        text(ox,oy+bh+23,f'{wide/10:g} × {high/10:g} cm',12)
    text(35,910,'尺寸以标注为准；复印比例可能变化 / STATED DIMENSIONS GOVERN; REPRODUCTION MAY ALTER SCALE',13)
    text(35,937,'吊耳详图、允许组合、实际吊具、系固点、支点及索力/结构能力须见批准运输图及计算书。',13)
    text(35,965,'See approved drawings for lifting-lug details, combinations, rigging, securing points and structural checks.',13)
    return E.tostring(svg,encoding='utf-8',xml_declaration=True)
