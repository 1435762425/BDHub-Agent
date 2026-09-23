export type MarketRuntimeState="ready"|"paused"|"planned";
export type MarketCapabilities={campaignCatalog:true;fullManagedCatalog:boolean|null};
export type MarketAccounts={communications:string|null;supply:string|null};
export type MarketSummary={key:string;label:string;shortLabel:string;runtimeState:MarketRuntimeState;contentReady:boolean;locale:string|null;currency:string|null;timeZone:string|null;platformRegion:string;templateLanguage:string|null;accounts:MarketAccounts;capabilities:MarketCapabilities};
export type MarketRegistry={defaultMarket:string;markets:MarketSummary[]};
