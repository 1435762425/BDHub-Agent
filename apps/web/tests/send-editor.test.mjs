import test from 'node:test';
import assert from 'node:assert/strict';
import {emptySendEditor,receiveSendState} from '../src/features/second-outreach/send-editor.ts';
const state=(revision,window=['16:30','24:00'])=>({market:'it',control:{revision,window,automaticEnabled:false,template:'standard'},runtime:{confirmedToday:revision}});

test('operator edits survive repeated polls while live runtime counts advance',()=>{
 let editor=receiveSendState(emptySendEditor,state(1));
 editor={...editor,draft:{...editor.draft,window:['17:15','23:30'],template:'new-template'}};
 for(let tick=0;tick<3;tick++)editor=receiveSendState(editor,{...state(1),runtime:{confirmedToday:tick+2}});
 assert.deepEqual(editor.draft.window,['17:15','23:30']);assert.equal(editor.draft.template,'new-template');
 assert.equal(editor.data.runtime.confirmedToday,4);assert.equal(editor.conflict,false);
});

test('another window changing the revision preserves local edits and exposes conflict',()=>{
 let editor=receiveSendState(emptySendEditor,state(1));editor={...editor,draft:{...editor.draft,automaticEnabled:true}};
 editor=receiveSendState(editor,state(2,['18:00','24:00']));
 assert.equal(editor.conflict,true);assert.equal(editor.draft.automaticEnabled,true);
 assert.deepEqual(editor.draft.window,['16:30','24:00']);
 const saved={...state(3),control:{...state(3).control,...editor.draft}};
 editor=receiveSendState(editor,saved,true);
 assert.equal(editor.conflict,false);assert.equal(editor.data.control.revision,3);
 assert.equal(receiveSendState(editor,state(2)),editor);
});

test('clean editor follows saved settings and stopping does not discard a dirty draft',()=>{
 let editor=receiveSendState(emptySendEditor,state(1));editor=receiveSendState(editor,state(2,['17:00','24:00']));
 assert.deepEqual(editor.draft.window,['17:00','24:00']);
 editor={...editor,draft:{...editor.draft,window:['18:00','24:00']}};
 editor=receiveSendState(editor,{...state(3),runtime:{state:'stopped'}});
 assert.deepEqual(editor.draft.window,['18:00','24:00']);assert.equal(editor.data.runtime.state,'stopped');
});
