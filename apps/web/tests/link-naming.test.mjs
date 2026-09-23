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
 assert.deepEqual(validateNamingRequest({action:'save',market:'it',config}),{action:'save',market:'it',config});
 assert.equal(validateNamingRequest({action:'preview',market:'it',config}).action,'preview');
 const brConfig={...config,version:'link-naming-br-v1',market:'br',language:'pt',locale:'pt-BR'};
 assert.deepEqual(validateNamingRequest({action:'save',market:'br',config:brConfig}),
  {action:'save',market:'br',config:brConfig});
 for(const bad of [{action:'delete',config},{action:'save'},{action:'save',config:{...config,template:'x'}},
                   {action:'save',market:'br',config:{...brConfig,language:'it'}},
                   {action:'save',market:'xx',config},{action:'save',config,extra:true},null]){
  assert.throws(()=>validateNamingRequest(bad),/invalid_link_naming_request/);
 }
});

test('a localized naming state is pinned to the requested market locale',()=>{
 const brConfig={...config,version:'link-naming-br-v1',market:'br',language:'pt',locale:'pt-BR'};
 assert.equal(validateNaming({...payload,config:brConfig},'br').config.locale,'pt-BR');
 assert.throws(()=>validateNaming({...payload,config:{...brConfig,locale:'it-IT'}},'br'),/invalid_link_naming/);
 assert.throws(()=>validateNaming({...payload,config:brConfig},'uk'),/invalid_link_naming/);
});
