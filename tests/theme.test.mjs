import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../static/theme.js',import.meta.url),'utf8');

function boot({stored=null,systemDark=false,query=''}={}){
  const root={attrs:{},dataset:{},style:{},setAttribute(key,value){this.attrs[key]=value;if(key==='data-theme')this.dataset.theme=value;},getAttribute(key){return this.attrs[key]??null;}};
  const meta={attrs:{content:'#f5f6fa'},setAttribute(key,value){this.attrs[key]=value;}};
  const store=new Map(stored===null?[]:[['yanshu-theme',stored]]);
  const document={documentElement:root,querySelector:selector=>selector.includes('theme-color')?meta:null};
  const localStorage={getItem:key=>store.get(key)??null,setItem:(key,value)=>store.set(key,value),removeItem:key=>store.delete(key)};
  const window={matchMedia:()=>({matches:systemDark,addEventListener(){}}),document,localStorage};
  const sandbox={document,localStorage,location:{search:query},URLSearchParams,window,console};
  vm.createContext(sandbox);
  vm.runInContext(source,sandbox);
  return {root,theme:root.dataset.theme,colorScheme:root.style.colorScheme,meta:meta.attrs.content,themeApi:window.yanshuTheme,stored:()=>store.get('yanshu-theme')};
}

test('theme bootstrap follows stored choice, then system preference, and sets theme-color',()=>{
  assert.equal(boot().theme,'light');
  assert.equal(boot({systemDark:true}).theme,'dark');
  const stored=boot({stored:'dark',systemDark:false});
  assert.equal(stored.theme,'dark');
  assert.equal(stored.colorScheme,'dark');
  assert.equal(stored.meta,'#0c1018');
  assert.equal(boot({stored:'sepia',systemDark:true}).theme,'dark');
});

test('explicit theme query overrides storage without persisting a new choice',()=>{
  const light=boot({stored:'dark',systemDark:true,query:'?theme=light'});
  assert.equal(light.theme,'light');
  assert.equal(light.stored(),'dark');
  assert.equal(boot({stored:'light',query:'?theme=dark'}).theme,'dark');
  assert.equal(boot({stored:'light',query:'?theme=sepia'}).theme,'light');
});

test('toggle flips the resolved theme and persists it for the next load',()=>{
  const page=boot({stored:'light'});
  assert.equal(page.themeApi.current(),'light');
  assert.equal(page.themeApi.toggle(),'dark');
  assert.equal(page.root.dataset.theme,'dark');
  assert.equal(page.stored(),'dark');
  assert.equal(page.themeApi.toggle(),'light');
  assert.equal(page.stored(),'light');
  page.themeApi.system();
  assert.equal(page.stored(),undefined);
});
