"""Representative sack patches on allocated volume, never individual cargo coordinates."""
import math

def patches(zones,max_patches=360):
    out=[]
    zones=[q for q in zones if min(q['l'],q['w'],q['h'])>=250 or (q.get('access_kind')=='user_confirmed_top' and min(q['l'],q['w'])>=250 and q['h']>=80)]
    if not zones:return out
    budget=max(1,max_patches//len(zones))
    for zone_index,q in enumerate(zones):
        local=[]
        x,y,z,l,w,h=[float(q[k]) for k in ('x','y','z','l','w','h')]
        faces=(((x,y,z),(l,0,0),(0,0,h)),((x,y+w,z),(l,0,0),(0,0,h)),((x,y,z),(0,w,0),(0,0,h)),((x+l,y,z),(0,w,0),(0,0,h)),((x,y,z),(l,0,0),(0,w,0)),((x,y,z+h),(l,0,0),(0,w,0)))
        for origin,u,v in faces:
            nu=max(1,min(18,math.ceil(math.sqrt(sum(a*a for a in u))/650)));nv=max(1,min(6,math.ceil(math.sqrt(sum(a*a for a in v))/450)))
            while nu*nv>max(1,budget//6):
                if nu>=nv:nu-=1
                else:nv-=1
            for j in range(nv):
                for i in range(nu):
                    def point(a,b):return [origin[k]+u[k]*a+v[k]*b for k in range(3)]
                    local.append({'corners':[point(i/nu,j/nv),point((i+1)/nu,j/nv),point((i+1)/nu,(j+1)/nv),point(i/nu,(j+1)/nv)],'tone':(i+j)%2,'zone_index':zone_index})
        # Sample the whole surface rather than truncating one end.
        take=min(budget,len(local),max_patches-len(out))
        out.extend(local[min(len(local)-1,int(j*len(local)/take))] for j in range(take))

    return out

def svg_sack(parent,corners,make,fill='#f6efdf'):
    """Curved pillow outline and stitched mouth within the projected surface patch."""
    def p(u,v):
        a,b,c,d=corners
        return ((1-v)*((1-u)*a[0]+u*b[0])+v*((1-u)*d[0]+u*c[0]),(1-v)*((1-u)*a[1]+u*b[1])+v*((1-u)*d[1]+u*c[1]))
    def pair(a):return f'{a[0]:.2f},{a[1]:.2f}'
    commands=[('M',[(.17,.1)]),('Q',[(.5,.02),(.83,.1)]),('C',[(.92,.17),(.99,.32),(.96,.54)]),('Q',[(.99,.82),(.82,.91)]),('Q',[(.5,.98),(.18,.91)]),('C',[(.01,.82),(.04,.66),(.04,.5)]),('Q',[(.01,.23),(.17,.1)])]
    path=' '.join(k+' '+' '.join(pair(p(*a)) for a in args) for k,args in commands)+' Z'
    make(parent,'path',{'d':path,'fill':fill,'fill-opacity':'.66','stroke':'#ae9a77','stroke-width':'.8','data-bag-glyph':'representative-only'})
    for a,b in (((.23,.14),(.77,.14)),((.17,.2),(.29,.28)),((.83,.2),(.71,.28))):
        x1,y1=p(*a);x2,y2=p(*b);make(parent,'line',{'x1':str(x1),'y1':str(y1),'x2':str(x2),'y2':str(y2),'stroke':'#9f8963','stroke-width':'.75'})
