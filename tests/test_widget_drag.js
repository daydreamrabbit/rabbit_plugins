const fs=require('fs'),vm=require('vm'),assert=require('assert');
const code=fs.readFileSync(require('path').join(__dirname,'../dashboard.js'),'utf8');
const block=code.slice(code.indexOf('  function enableDragScroll('),code.indexOf('  enableDragScroll(tabs);'));
class Element {constructor(){this.events={};this.scrollLeft=100;this.capture=false;this.classList={add(){},remove(){}};}addEventListener(k,f){this.events[k]=f;}setPointerCapture(){this.capture=true;}hasPointerCapture(){return this.capture;}releasePointerCapture(){this.capture=false;}}
const ctx={};vm.createContext(ctx);vm.runInContext(block,ctx);const e=new Element();ctx.enableDragScroll(e);
const event={pointerId:1,pointerType:'mouse',button:0,buttons:1,clientX:100,preventDefault(){}};
e.events.pointerdown(event);e.events.pointermove({...event,clientX:60});assert.equal(e.scrollLeft,140);e.events.pointerup(event);assert.equal(e.capture,false);
let blocked=false;e.events.click({detail:1,preventDefault(){},stopImmediatePropagation(){blocked=true;}});assert(blocked);
e.events.pointerdown(event);e.events.pointermove({...event,clientX:98});e.events.pointerup(event);blocked=false;e.events.click({detail:1,preventDefault(){},stopImmediatePropagation(){blocked=true;}});assert(!blocked);
e.events.pointerdown({...event,pointerType:'touch'});e.events.pointermove({...event,pointerType:'touch',clientX:0});assert.equal(e.scrollLeft,140);
console.log('PASS drag scroll, click suppression, normal click, native touch');
