import type { MatchingBatch, MatchMarket, MatchCurrency, ContentFormat, ProductInput, CreatorInput, FactSource } from "../../features/matching/contracts.ts";

const markets:MatchMarket[]=["mx","br","it"];
const currencies:Record<MatchMarket,MatchCurrency>={mx:"MXN",br:"BRL",it:"EUR"};
const categories=["beauty","home","electronics","sport","fashion","kitchen","pets","wellness"];
const names:Record<string,string>={beauty:"护理工具",home:"家居收纳",electronics:"数码配件",sport:"运动装备",fashion:"日常配饰",kitchen:"厨房工具",pets:"宠物用品",wellness:"日常护理"};

/** Entirely generated fixtures. Numeric platform-shaped identifiers remain strings. */
export function makeMatchingFixture(options:{products?:number;creators?:number;now?:number}={}):MatchingBatch {
  const productCount=options.products??72,creatorCount=options.creators??360,now=options.now??Date.now();
  for(const count of [productCount,creatorCount])if(!Number.isSafeInteger(count)||count<0||count>100_000)throw new Error("Fixture counts must be integers between 0 and 100000.");
  const source=(ref:string,window=false):FactSource=>({ref:`synthetic:${ref}`,observedAt:now,windowStart:window?now-30*86_400_000:null,windowEnd:window?now:null});
  const products:ProductInput[]=[],creators:CreatorInput[]=[];
  const offers:NonNullable<MatchingBatch["offers"]>=[];
  const evidence:NonNullable<MatchingBatch["evidence"]>=[],demands:NonNullable<MatchingBatch["demands"]>=[];
  for(let i=0;i<productCount;i++) {
    const market=markets[i%3],category=categories[Math.floor(i/3)%categories.length],n=String(i+1).padStart(6,"0");
    const product:ProductInput={id:`synthetic-product-${market}-${n}`,market,pid:`99000000000000${n}`,title:`[合成] ${names[category]} ${n}`,image:`/images/product/product-0${i%5+1}.jpg`,categories:i%37===36?[]:i%4===0?[category,categories[(Math.floor(i/3)+1)%categories.length]]:[category],formats:i%3===0?["video","live"]:[i%2===0?"video":"live"],description:`合成 ${category} 商品，仅用于验证结构召回。`,priceMinor:i%19===18?null:(Math.floor(i/3)%8+1)*(market==="mx"?10000:market==="br"?5000:1500),currency:currencies[market],source:source(`product/${n}`)};
    products.push(product);
    for(let j=0;j<(i%4===0?2:1);j++)offers.push({id:`synthetic-offer-${n}-${j}`,productId:product.id,campaignId:`synthetic-campaign-${market}-${j}`,accountRef:`synthetic-account-${market}-${j}`,publicCommissionBps:800+j*100,totalCommissionBps:1500+j*100,creatorCommissionBps:i%17===16?null:1200+j*100,agencyCommissionBps:i%17===16?null:300,stock:i%23===22?null:i%13===12?0:50+j*30,sampleAvailable:i%7===6?null:i%2===0,sampleQuota:i%7===6?null:i%2===0?10:0,startsAt:now-7*86_400_000,endsAt:now+(i%29===28?-1:30)*86_400_000,cardStatus:i%5===4?"needs_preparation":"verified",source:source(`offer/${n}/${j}`)});
  }
  const byMarket=new Map(markets.map(m=>[m,products.filter(p=>p.market===m)]));
  for(let i=0;i<creatorCount;i++) {
    const market=markets[i%3],category=categories[Math.floor(i/3)%categories.length],n=String(i+1).padStart(6,"0"),unit=market==="mx"?10000:market==="br"?5000:1500;
    const formats:ContentFormat[]=i%4===0?["video","live"]:[i%2===0?"video":"live"];
    const creator:CreatorInput={id:`synthetic-creator-${market}-${n}`,market,oecId:`88000000000000${n}`,name:`[合成] ${market.toUpperCase()} 达人 ${n}`,avatar:`/images/user/user-${String(17+i%10).padStart(2,"0")}.jpg`,categories:i%31===30?[]:[category,categories[(Math.floor(i/3)+3)%categories.length]],formats:i%41===40?[]:formats,bio:`合成 ${category} 与 ${categories[(Math.floor(i/3)+3)%categories.length]} 内容创作者。`,priceMinMinor:i%17===16?null:unit,priceMaxMinor:i%17===16?null:unit*(Math.floor(i/3)%8+2),currency:currencies[market],control:i%47===46?"human":i%53===52?"paused":"auto",marketingStopped:i%59===58,source:source(`creator/${n}`,true)};
    creators.push(creator);
    const available=byMarket.get(market)??[],product=available[Math.floor(i/3)%Math.max(available.length,1)];
    if(product&&i%5!==4)evidence.push({id:`synthetic-evidence-${n}`,creatorId:creator.id,market,pid:product.pid,units:i%11===10?0:1+i%30,format:formats[0],source:source(`report/${n}`,true)});
    if(product&&i%13===0)demands.push({id:`synthetic-demand-${n}`,creatorId:creator.id,productId:i%26===0?product.id:null,categories:[category],active:true,source:source(`request/${n}`)});
  }
  return {products,creators,offers,evidence,demands};
}
