const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const docs={},wins={},label={textContent:''};let confirmed=true;
const a={name:'weight',value:'100',type:'number'},b={name:'length',value:'200',type:'number'};
const forms=[{elements:[a]},{elements:[b]}];a.form=forms[0];b.form=forms[1];
const document={querySelectorAll:s=>s==='form'?forms:[],getElementById:id=>id==='formDirtyCount'?label:null,addEventListener:(n,f)=>docs[n]=f};
const window={addEventListener:(n,f)=>wins[n]=f};
const source=fs.readFileSync(path.join(__dirname,'../app/static/compact-ui.js'),'utf8');
vm.runInNewContext(source.slice(source.indexOf('// Filters keep')),{document,window,confirm:()=>confirmed});
function blocked(){let value=false;wins.beforeunload({preventDefault(){value=true;}});return value;}
assert(!blocked());a.value='150';docs.input({target:a});assert(blocked());assert(label.textContent.includes('1'));
wins.submit({target:forms[0]});assert(blocked());assert(label.textContent.includes('1')); // failed/cancelled attempt
b.value='300';docs.change({target:b});confirmed=false;let cancelled=false;
wins.submit({target:forms[0],preventDefault(){cancelled=true;},stopImmediatePropagation(){}});
assert(cancelled);assert(blocked());assert(label.textContent.includes('2'));
confirmed=true;wins.submit({target:forms[0]});assert(blocked());assert(label.textContent.includes('2')); // no success yet
a.value='100';docs.input({target:a});b.value='200';docs.change({target:b});assert(!blocked());assert.equal(label.textContent,'');
console.log('Unsaved-form checks passed: clean, edit, failed submit, cancelled submit, multiple forms, accepted attempt, restore.');
