import type {FactSource} from "./contracts.ts";

/** Discovery evidence only; historical labels do not certify present-day category or eligibility. */
export interface CategoryFact {
  status:"historical"|"conflict"|"missing";
  namespace:string;
  sourceLabels:string[];
  source:FactSource;
  transformVersion:string|null;
  timeBasis:"field_observation"|"batch_completed";
  note:string;
}
