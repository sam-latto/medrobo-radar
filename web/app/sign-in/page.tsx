import { Suspense } from "react";
import { Logo } from "@/components/logo";
import { SignInForm } from "./sign-in-form";

export const metadata = { title: "Sign in · Industry Radar" };

export default function SignInPage() {
  return (
    <main className="grid min-h-screen grid-cols-1 desk:grid-cols-2">
      <section className="flex flex-col justify-between gap-10 bg-teal-dark px-5 py-8 text-white desk:px-16 desk:py-14">
        <Logo onDark />
        <div className="flex max-w-[480px] flex-col gap-7">
          <h1 className="text-[32px] font-semibold leading-[1.15] tracking-[-0.025em] desk:text-display">
            Know what moved in your industry today.
          </h1>
          <p className="text-[17px] leading-[1.6] text-on-dark">
            Daily briefings, company momentum and matching roles across the industries you follow, from FDA
            filings, SEC filings, research and the news.
          </p>
        </div>
        <span className="text-small text-on-dark-muted">Healthcare · Technology · Consulting · Private equity · and more</span>
      </section>

      <section className="flex items-center justify-center px-4 py-10 desk:px-16 desk:py-14">
        <div className="flex w-full max-w-[380px] flex-col gap-6">
          <div className="flex flex-col gap-2">
            <h2 className="text-h2">Sign in</h2>
            <p className="text-body-lg text-muted">
              Use your University of Michigan Google account. Your resume, job preferences and saved industries stay
              private to you.
            </p>
          </div>
          <Suspense>
            <SignInForm />
          </Suspense>
        </div>
      </section>
    </main>
  );
}
