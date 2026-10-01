import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { getProfile } from "@/lib/auth";
import { signOut } from "./(auth)/actions";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Freelancer Bid Bot",
  description: "Shared Freelancer job feed with AI proposals",
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  const profile = await getProfile();
  const approved = profile?.status === "approved";
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col font-sans">
        <header className="border-b border-border bg-surface">
          <nav className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3 text-sm">
            <Link href="/jobs" className="font-semibold">Freelancer Bid Bot</Link>
            {approved && (
              <>
                <Link href="/jobs" className="text-muted hover:text-fg">Jobs</Link>
                <Link href="/settings" className="text-muted hover:text-fg">My settings</Link>
                {profile.role === "admin" && (
                  <Link href="/admin" className="text-muted hover:text-fg">Admin</Link>
                )}
              </>
            )}
            {profile && (
              <form action={signOut} className="ml-auto flex items-center gap-3">
                <span className="hidden text-muted sm:inline">{profile.email}</span>
                <button className="btn">Sign out</button>
              </form>
            )}
          </nav>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
