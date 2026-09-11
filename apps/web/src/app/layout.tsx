import type {Metadata} from "next";
import "./globals.css";
import {DemoProvider} from "@/features/bdhub/store";
import {ThemeProvider} from "@/context/ThemeContext";
export const metadata:Metadata={title:"BDHub Agent · 运营工作台",description:"基于 TailAdmin 免费版的可交互前端原型。全部业务数据为演示。",icons:{icon:"/favicon.svg"}};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="zh-CN" suppressHydrationWarning><body className="text-gray-800 dark:bg-gray-900 dark:text-gray-200"><ThemeProvider><DemoProvider>{children}</DemoProvider></ThemeProvider></body></html>;}
