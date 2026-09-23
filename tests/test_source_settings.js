const fs = require('fs'), vm = require('vm'), assert = require('assert');
class Element {
 constructor(){this.children=[];this.dataset={};this.events={};this.classList={toggle(){},add(){},remove(){}};}
 set innerHTML(s){if(s.includes('<input')){const input=new Element();input.dataset.source=s.match(/data-source="([^"]+)"/)[1];this.children=[input];}}
 append(child){this.children.push(child);}
 replaceChildren(){this.children=[];}
 addEventListener(name,fn){this.events[name]=fn;}
 querySelector(selector){return this.querySelectorAll(selector)[0];}
 querySelectorAll(selector){const all=this.children.flatMap(c=>[c,...c.querySelectorAll('*')]);if(selector==='*')return all;return all.filter(c=>selector==='input'?!!c.dataset.source&&!c.className:selector==='.rabbit-metadata-source-row'?c.className===selector.slice(1):selector==='[data-source]'?!!c.dataset.source:(selector===`[data-source="${c.dataset.source}"]` || (selector===`input[data-source="${c.dataset.source}"]`&&!c.className)));}
}
const code=fs.readFileSync(require('path').join(__dirname,'../settings.js'),'utf8');
const block=code.slice(code.indexOf('  if (sourceList && sourceInput && fieldInput)'),code.indexOf('\n})();'));
for(const initial of ['ridi','']){
 const sourceList=new Element(),sourceInput={},fieldInput={};
 vm.runInNewContext(block,{sourceList,sourceInput,fieldInput,savedConfig:{metadata_sources:initial},root:{querySelectorAll:()=>[]},syncChoiceState(){},document:{createElement:()=>new Element()}});
 const input=key=>sourceList.querySelector(`input[data-source="${key}"]`);
 const toggle=(key,checked)=>{const el=input(key);el.checked=checked;el.events.change({target:el});};
 toggle('munpia',true);toggle('ridi',false);
 for(let n=0;n<3;n++){
 const from=sourceList.children.findIndex(r=>r.dataset.source==='munpia');
 sourceList.children[0].events.drop({preventDefault(){},dataTransfer:{getData:()=>String(from)}});
 assert.equal(input('munpia').checked,true);assert.equal(input('ridi').checked,false);assert.equal(sourceInput.value,'munpia');
 }
 const reopenedList=new Element(), reopenedValue={};
 vm.runInNewContext(block,{sourceList:reopenedList,sourceInput:reopenedValue,fieldInput:{},savedConfig:{metadata_sources:sourceInput.value},root:{querySelectorAll:()=>[]},syncChoiceState(){},document:{createElement:()=>new Element()}});
 assert.equal(reopenedValue.value,'munpia');
 assert.equal(reopenedList.children[0].dataset.source,'munpia');
 assert.equal(reopenedList.querySelector('input[data-source="munpia"]').checked,true);
 toggle('munpia',false);
 sourceList.children[1].events.drop({preventDefault(){},dataTransfer:{getData:()=> '0'}});
 assert.equal(sourceInput.value,'');
 console.log('PASS selection and deselection survive reorder; initial='+JSON.stringify(initial));
}
