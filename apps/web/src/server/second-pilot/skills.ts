import type {SecondPilotDraft,SecondPilotProduct} from "../../features/second-pilot/contracts.ts";

export const SECOND_INTRO_SKILL={id:"it-second-intro",version:"1",language:"it",method:"deterministic",purpose:"Explore interest in the exact observed products without inventing current commercial terms."} as const;

// Short names grounded in the original Italian product titles; no new efficacy or offer claims.
export const ITALIAN_PRODUCT_NAMES:Record<string,string>={
  "1729779362302171335":"compresse per la bocca Freegrin",
  "1729480019490150432":"cuscino cervicale",
  "1729502070782139035":"leggings da yoga",
  "1729584712875612996":"body modellante da donna",
  "1729480472768911992":"multimetro digitale NJTY T3",
};

export function draftSecondIntro(handle:string,products:SecondPilotProduct[]):SecondPilotDraft {
  if(!/^[A-Za-z0-9._]{1,100}$/.test(handle)||!products.length||products.length>5)throw new Error("Invalid draft recipient or product scope.");
  if(new Set(products.map(p=>p.pid)).size!==products.length)throw new Error("A draft cannot repeat a product.");
  for(const product of products)if(!ITALIAN_PRODUCT_NAMES[product.pid]||product.italianName!==ITALIAN_PRODUCT_NAMES[product.pid])throw new Error("Product name requires a grounded Italian label.");
  const names=products.map(p=>`«${p.italianName}»`);
  const productText=names.length===1?names[0]:`${names.slice(0,-1).join(", ")} e ${names[names.length-1]}`;
  const text=`Ciao @${handle}! Ti scrivo per valutare una possibile collaborazione su ${productText}. Se ti interessa, possiamo verificare insieme le condizioni disponibili. Ti va di parlarne?`;
  if(text.length>900)throw new Error("Draft exceeds the bounded outreach context.");
  return {text,language:"it",method:"deterministic",skillVersion:`${SECOND_INTRO_SKILL.id}@${SECOND_INTRO_SKILL.version}`,claimRefs:products.map(p=>p.source.ref)};
}
