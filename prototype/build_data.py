import json, math
seen=set(); out=[]
for line in open('raw.txt'):
    line=line.strip()
    if not line: continue
    O,T,c=line.split('|')
    pts=[tuple(map(float,p.split(','))) for p in c.split()]
    # outer ring: stop at first return to start
    ring=[pts[0]]
    for p in pts[1:]:
        if p==pts[0]: break
        ring.append(p)
    key=(round(ring[0][0],7),round(ring[0][1],7),O)
    if key in seen: continue
    seen.add(key)
    O=float(O);T=float(T)
    if O-T<2.5 or len(ring)<3: continue
    out.append({'o':O,'t':T,'r':[[round(x,6),round(y,6)] for x,y in ring]})
# generated round building (simplified circle) south of Siebensterngasse
cx,cy=16.356123,48.201748
out.append({'o':84.4,'t':39.7,'r':[[round(cx+0.00029*math.cos(a*math.pi/12),6),round(cy+0.000193*math.sin(a*math.pi/12),6)] for a in range(24)]})
print(len(out))
json.dump(out,open('buildings.json','w'),separators=(',',':'))
