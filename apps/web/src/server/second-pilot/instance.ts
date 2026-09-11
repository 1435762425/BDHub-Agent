import {resolve} from "node:path";
import {SecondPilotStore} from "./store.ts";
const local=globalThis as unknown as {bdhubSecondPilot?:SecondPilotStore};
export function getSecondPilotStore(){return local.bdhubSecondPilot??=new SecondPilotStore(resolve("../../var/second-italy.sqlite"));}
