import {redirect} from "next/navigation";
import {readMarketRegistry} from "@/server/markets/registry";
export default function Home(){redirect(`/${readMarketRegistry().defaultMarket}`);}
