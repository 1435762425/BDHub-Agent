import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateNaming,validateNamingRequest} from '../src/server/link-naming/bridge.ts';

const config={version:'link-naming-v1',template:'BJN {short_name} {creator_percent}% {tail}',tailLength:6,maxLength:50,shortNameMaxLength:30};
const preview=[{pid:'1729480061238089885',campaignId:'7685262119046498070',title:'Quaderno',creatorPercent:'13',publicPercent:'12',totalPercent:'14',shortName:'quaderni',name:'BJN quaderni 13% 0ce672',tail:'0ce672',length:24,limit:50,error:null}];
const payload={config,fingerprint:'c27a68f2084d14c7f4213a9faaa44b097a6c904c9967219718e85389da5845c7',placeholders:['short_name','creator_percent','tail'],preview};

test('a naming state is accepted and keeps the rendered preview',()=>{
 const v=validateNaming(payload);
 assert.equal(v.config.template,'BJN {short_name} {creator_percent}% {tail}');
 assert.equal(v.preview[0].name,'BJN quaderni 13% 0ce672');
 assert.equal(v.preview[0].length,24);
});

test('unknown placeholders, bad ranges and missing short name are rejected',()=>{
 for(const bad of [{...payload,config:{...config,template:'BJN {pid} {tail}'}},
                   {...payload,config:{...config,template:'BJN {short_name} {tail'}},
                   {...payload,config:{...config,tailLength:2}},
                   {...payload,config:{...config,maxLength:80}},
                   {...payload,fingerprint:'not-a-hash'},
                   {...payload,placeholders:['short_name','evil']},
                   {...payload,preview:[{...preview[0],name:42}]},
                   {...payload,preview:[{...preview[0],pid:'nope'}]}]){
  assert.throws(()=>validateNaming(bad),/invalid_link_naming/);
 }
});

test('save and preview requests are validated before reaching the platform bridge',()=>{
 assert.deepEqual(validateNamingRequest({action:'save',config}),{action:'save',config});
 assert.equal(validateNamingRequest({action:'preview',config}).action,'preview');
 for(const bad of [{action:'delete',config},{action:'save'},{action:'save',config:{...config,template:'x'}},null]){
  assert.throws(()=>validateNamingRequest(bad),/invalid_link_naming_request/);
 }
});
