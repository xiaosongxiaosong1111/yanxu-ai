import test from 'node:test';
import assert from 'node:assert/strict';
import {renderSettings,normalizeSettings} from '../static/settings.mjs';

const provider={id:'local_bge',label:'本机中文向量模型',requires_key:false,default_model:'BAAI/bge-small-zh-v1.5',default_base_url:'',local:true};
const state={loadStatus:'ready',tab:'embedding',providers:[{id:'deepseek',label:'DeepSeek'}],embeddingProviders:[provider],saved:{ai:{provider:'deepseek',model:'chat-model',status:'connected',has_key:true},yuanbao:{status:'connected',has_key:true},embedding:{provider:'local_bge',model:provider.default_model,status:'not_configured',has_key:false}},embedding:{providers:[provider],draft:{provider:'local_bge',model:provider.default_model},loadStatus:'ready'}};

test('a third knowledge tab shows independent embedding configuration and a local-first explanation',()=>{
 const html=renderSettings(state);
 assert.match(html,/data-settings-tab="embedding"/);assert.match(html,/知识检索/);assert.match(html,/本机中文向量模型/);
 assert.match(html,/首次/);assert.match(html,/95/);assert.match(html,/测试并保存/);
 assert.ok(!html.includes('DeepSeek</option>'));assert.ok(!html.includes('name="secret_key"'));
});

test('public embedding state preserves observed identity without leaking credentials',()=>{
 const privateFixture='PRIVATE_CANARY', tokenFixture='TOKEN_CANARY';
 const value=normalizeSettings({embedding:{provider:'local_bge',model:'bge',dimension:512,status:'connected',response_model:'observed-bge',api_key:privateFixture,token:tokenFixture}});
 assert.equal(value.embedding?.dimension,512);assert.equal(value.embedding?.response_model,'observed-bge');
 assert.ok(!JSON.stringify(value).includes('PRIVATE_CANARY'));assert.ok(!JSON.stringify(value).includes('TOKEN_CANARY'));
});

test('unconfigured embedding never labels the semantic index as ready',()=>{
 const html=renderSettings(state);assert.match(html,/语义索引/);assert.ok(!html.includes('语义索引已就绪'));assert.ok(!html.includes('向量生成已验证'));
});
