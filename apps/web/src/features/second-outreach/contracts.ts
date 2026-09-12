export interface SecondProduct {id:string;pid:string;title:string;nameIt:string;units:number;windowStart:number;windowEnd:number;sourceRef:string;}
export interface SecondRecipient {creatorId:string;oecId:string;handle:string|null;observedAt:string;}
export interface SecondOpportunity {
  id:string;sourceCreatorId:string;sourceHandle:string;sourceNamespace:"kalodata";sourceExternalId:string;
  currentRecipient:SecondRecipient|null;products:SecondProduct[];historicalOwnership:"unverified";
  fingerprint:string;revision:number;control:"active"|"paused";packetId:string|null;templateDraft?:SecondTemplateDraft|null;
}
export interface SecondOutreachStatus {
  market:"it";opportunities:number;identified:number;unresolved:number;products:number;edges:number;paused:number;
  sourceFingerprint:string|null;updatedAt:string|null;realSends:number;resultUnknown:number;
  transport:{state:"not_tested"|"auth_verified"|"blocked";reason:string|null;observedAt:string|null;sendEnabled:false};
}
export interface SecondOutreachList {items:SecondOpportunity[];total:number;offset:number;limit:number;}
export interface SecondPreparedPacket {packetId:string;opportunityId:string;sourceFingerprint:string;createdAt:string;executionBlocked:true;}
export interface SecondTemplate {id:string;name:string;description:string;version:number;fingerprint:string;textIt:string;translationZh:string;requiresCard:true;deliveryOrder:"card_then_text";}
export interface SecondTemplateDraft {draftId:string;opportunityId:string;templateId:string;templateName:string;templateVersion:number;templateFingerprint:string;sourceFingerprint:string;contextFingerprint:string;revision:number;createdAt:string;textIt:string;translationZh:string;textSha256:string;recipient:SecondRecipient;requiresCard:true;requiredCardPids:string[];deliveryOrder:"card_then_text";promotionClaims?:{kind:"commission_above_public";pid:string;creatorPercent:string;publicPercent:string;sourceRef:string;observedAt:string}[];executionBlocked:true;}
export interface SecondTemplateBatch {batchId:string;templateId:string;sourceFingerprint:string;prepared:SecondTemplateDraft[];skipped:{opportunityId:string;reason:string}[];modelCalls:0;}
