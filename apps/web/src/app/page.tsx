import {redirect} from "next/navigation";
import {readMarketRegistry} from "@/server/markets/registry";
// The first screen is the all-market business overview; each market keeps its own operations page.
export default function Home(){redirect(`/${readMarketRegistry().defaultMarket}/console`);}
