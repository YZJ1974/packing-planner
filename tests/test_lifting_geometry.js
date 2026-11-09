const assert=require('node:assert/strict');
const {buildModel}=require('../app/static/lifting.js');
const box={inner_h:2400,placements:[{seq:1,x:100,y:200,z:300,l:2000,w:1000,h:800,weight_kg:1000}]};
let m=buildModel(box);const i=m.items[0];
assert.equal(i.points.length,4);assert.deepEqual(i.cg,[1100,700,700]);assert.equal(i.points[0][2],1100);
for(const p of i.points){const angle=Math.atan2(i.hook[2]-p[2],Math.hypot(p[0]-i.hook[0],p[1]-i.hook[1]))*180/Math.PI;assert.ok(angle>=60-1e-9);}
const original=JSON.stringify(box);buildModel(box,{angle:45});assert.equal(JSON.stringify(box),original);
const eccentric=structuredClone(box);eccentric.placements[0].cg_local_x=1900;
assert.equal(buildModel(eccentric).items[0].inside,false);
assert.equal(buildModel(box,{minWeight:2000}).items.length,0);
assert.throws(()=>buildModel(box,{angle:90}));assert.throws(()=>buildModel(box,{setback:0}));
const moved=structuredClone(box);moved.placements[0].z+=100;
assert.ok(Math.abs(buildModel(moved).items[0].hook[2]-i.hook[2]-100)<1e-9);
assert.ok(Math.abs(i.headroom-(2400-i.hook[2]))<1e-9);
console.log('8 lifting geometry checks passed: projection input, angle, immutability, eccentric warning, filtering, input limits, move synchronization, clearance.');
