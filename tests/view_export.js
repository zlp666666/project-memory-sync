// Exercise the actual generated browser handlers against a tiny deterministic DOM.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.value = ''; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  click() { if (this.onclick) this.onclick(); }
}
const ids = Object.fromEntries(['q','view','rows','count','export'].map(k => [k,new Element(k)]));
ids.view.value = 'all';
let captured;
const storage = new Map();
const document = {getElementById: k=>ids[k], createElement: tag=>new Element(tag)};
vm.runInNewContext(script, {document, Blob, URL:{createObjectURL: b=>{captured=b;return 'blob:synthetic';}, revokeObjectURL:()=>{}},
  localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)}});
ids.view.value='incub'; ids.view.onchange();
assert.equal(ids.rows.children.length, 1);
let select=ids.rows.children[0].children[2]; select.value='keep';select.onchange();
ids.view.value='all';ids.view.onchange();
assert.equal(ids.rows.children.length, 1);
select=ids.rows.children[0].children[2];assert.equal(select.value,'keep');
select.value='restart';select.onchange();
ids.export.onclick();
(async()=>{const exported=JSON.parse(await captured.text());assert.equal(exported.decisions.length,1);
 assert.equal(exported.decisions[0].intent,'restart');assert.equal(exported.requires_user_authorization,true);
 assert.match(exported.decisions[0].id,/^[0-9a-f-]{36}$/);console.log('stable view identity and incubation export passed');})().catch(e=>{console.error(e);process.exit(1)});
