import type {Metadata} from "next";
import "./globals.css";
import {ThemeProvider} from "@/context/ThemeContext";
export const metadata:Metadata={title:"BDHub Agent · 运营工作台",description:"达人身份、画像匹配与合作运营工作台。",icons:{icon:"/favicon.svg"}};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="zh-CN" suppressHydrationWarning><body className="text-gray-800 dark:bg-gray-900 dark:text-gray-200"><ThemeProvider>{children}</ThemeProvider></body></html>;}
