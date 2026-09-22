export type MarketCapabilities={campaignCatalog:boolean;fullManagedCatalog:boolean};
export type MarketSummary={key:string;label:string;shortLabel:string;locale:string;currency:string;timeZone:string;platformRegion:string;templateLanguage:string;capabilities:MarketCapabilities};
export type MarketRegistry={defaultMarket:string;markets:MarketSummary[]};
