import { ArrowRight, Database, Link2, Mic, PhoneCall, Users, Zap } from "lucide-react";

const NAV_LINKS = [
  { href: "#features", label: "Features" },
  { href: "#how-it-works", label: "How it works" },
  { href: "#pricing", label: "Pricing" },
  { href: "#docs", label: "Resources" },
];

const HOW_IT_WORKS = [
  {
    step: "01",
    title: "Listen & Understand",
    body: "Knotic uses low-latency LLMs to hear, process, and understand intent in milliseconds, allowing for natural barge-in.",
  },
  {
    step: "02",
    title: "Context Check",
    body: "The agent instantly queries your CRM, pricing data, and internal docs for grounded, non-hallucinated facts.",
  },
  {
    step: "03",
    title: "Action & Booking",
    body: "It checks availability across your team's calendars and books meetings directly or creates lead records in real-time.",
  },
  {
    step: "04",
    title: "Human Handoff",
    body: "For edge cases or complex negotiation, Knotic hot-transfers the live call to a human agent with a full summary.",
  },
];

const FEATURES = [
  {
    icon: Zap,
    tint: "bg-brand-secondary/10 text-brand-secondary",
    title: "Low-Latency Voice",
    body: "Proprietary voice infrastructure aims to keep conversations feeling real, without awkward pauses or mechanical delays.",
  },
  {
    icon: Database,
    tint: "bg-brand-primary/10 text-brand-primary",
    title: "Grounded Intelligence",
    body: "Connected directly to your data sources so Knotic answers from real pricing, product specs, and terms.",
  },
  {
    icon: Link2,
    tint: "bg-brand-accent/10 text-brand-accent",
    title: "Full Stack Integration",
    body: "Native hooks into your CRM and calendar so leads and meetings sync without a middleman tool.",
  },
  {
    icon: Users,
    tint: "bg-black/5 text-black",
    title: "Seamless Escalation",
    body: "Intelligent routing detects when a human touch is needed and transfers calls with instant context sync.",
  },
];

export default function HomePage() {
  return (
    <div className="min-h-screen">
      <nav className="fixed top-0 z-50 w-full border-b border-brand-border bg-brand-bg/90 backdrop-blur-md">
        <div className="mx-auto flex h-20 max-w-7xl items-center justify-between px-6">
          <div className="flex items-center gap-10">
            <a href="#" className="flex items-center">
              <span className="text-2xl font-bold tracking-tighter">KNOTIC</span>
            </a>
            <div className="hidden items-center gap-8 text-sm font-medium text-brand-muted md:flex">
              {NAV_LINKS.map((link) => (
                <a key={link.href} href={link.href} className="transition-colors hover:text-black">
                  {link.label}
                </a>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-4">
            <a
              href="#login"
              className="rounded-lg px-4 py-2 text-sm font-medium transition-colors hover:bg-brand-surface"
            >
              Log in
            </a>
            <a
              href="#demo"
              className="rounded-lg bg-brand-primary px-5 py-2.5 text-sm font-medium text-white shadow-sm transition-all hover:brightness-110"
            >
              Talk to the demo
            </a>
          </div>
        </div>
      </nav>

      <main className="pt-20">
        <section className="relative overflow-hidden pt-24 pb-32">
          <div className="mx-auto grid max-w-7xl items-center gap-16 px-6 lg:grid-cols-2">
            <div className="relative z-10">
              <div className="mb-8 inline-flex items-center gap-2 rounded-full border border-brand-border bg-brand-surface/50 px-3 py-1 text-xs font-semibold tracking-wide text-brand-muted uppercase">
                <span className="flex h-2 w-2 animate-pulse rounded-full bg-brand-primary" />
                Real-Time Voice Intelligence
              </div>
              <h1 className="text-balance mb-8 text-6xl leading-[1.1] font-bold tracking-tight lg:text-7xl">
                Sales conversations that feel <span className="text-brand-primary">human.</span>
              </h1>
              <p className="text-balance mb-10 max-w-xl text-xl leading-relaxed text-brand-muted">
                Knotic is a real-time AI voice agent that qualifies leads, answers product questions grounded in
                your own data, and books meetings on your team&apos;s calendars.
              </p>
              <div className="flex flex-col gap-4 sm:flex-row">
                <a
                  href="#demo-call"
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-brand-primary px-8 py-4 text-lg font-semibold text-white transition-all hover:-translate-y-0.5 hover:shadow-xl"
                >
                  <PhoneCall className="h-5 w-5" aria-hidden="true" />
                  Start a demo call
                </a>
                <a
                  href="#learn-more"
                  className="inline-flex items-center justify-center gap-2 rounded-xl border border-brand-border bg-white px-8 py-4 text-lg font-semibold text-black transition-colors hover:bg-brand-surface"
                >
                  Learn more
                </a>
              </div>
            </div>

            <div className="relative">
              <div className="relative rounded-3xl border border-brand-border bg-white p-8 shadow-2xl">
                <div className="mb-8 flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="flex h-12 w-12 items-center justify-center rounded-full bg-brand-primary/10">
                      <Mic className="h-6 w-6 text-brand-primary" aria-hidden="true" />
                    </div>
                    <div>
                      <div className="text-sm font-semibold">Incoming Lead Call</div>
                      <div className="text-xs text-brand-muted">Status: Real-time conversation</div>
                    </div>
                  </div>
                  <div className="rounded-full bg-green-100 px-3 py-1 text-xs font-bold tracking-wider text-green-700 uppercase">
                    Sample
                  </div>
                </div>

                <div className="space-y-6">
                  <div className="flex gap-4">
                    <div className="flex-1 rounded-2xl rounded-tl-none border border-brand-border bg-brand-bg p-4">
                      <p className="text-sm text-brand-muted italic">
                        &ldquo;Hi, I&apos;m looking for a tool that integrates with our CRM and handles pricing for
                        multi-seat licenses...&rdquo;
                      </p>
                    </div>
                  </div>
                  <div className="flex flex-row-reverse gap-4">
                    <div className="flex-1 rounded-2xl rounded-tr-none border border-brand-secondary/20 bg-brand-secondary/5 p-4">
                      <p className="text-sm font-medium">
                        &ldquo;Knotic connects to your CRM directly. For multi-seat licenses, I can walk you through
                        tiered pricing now, or book a deep-dive with a solutions engineer.&rdquo;
                      </p>
                    </div>
                  </div>
                </div>
              </div>
              <div className="absolute -top-20 -right-20 -z-10 h-80 w-80 rounded-full bg-brand-secondary/5 blur-3xl" />
              <div className="absolute -bottom-20 -left-20 -z-10 h-80 w-80 rounded-full bg-brand-primary/5 blur-3xl" />
            </div>
          </div>
        </section>

        <section id="how-it-works" className="bg-white py-32">
          <div className="mx-auto max-w-7xl px-6">
            <div className="mx-auto mb-20 max-w-3xl text-center">
              <h2 className="mb-6 text-4xl font-bold">The ultimate sales partner.</h2>
              <p className="text-lg text-brand-muted">
                Knotic handles the high-volume outreach and qualification so your human agents can focus on closing.
              </p>
            </div>

            <div className="grid gap-12 md:grid-cols-4">
              {HOW_IT_WORKS.map((item) => (
                <div key={item.step} className="group relative">
                  <div className="mb-6 text-5xl font-bold text-brand-surface transition-colors group-hover:text-brand-primary">
                    {item.step}
                  </div>
                  <h3 className="mb-4 text-xl font-bold">{item.title}</h3>
                  <p className="leading-relaxed text-brand-muted">{item.body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="features" className="border-y border-brand-border bg-brand-bg py-32">
          <div className="mx-auto max-w-7xl px-6">
            <div className="grid gap-8 lg:grid-cols-3">
              <div className="lg:col-span-1">
                <h2 className="mb-6 text-4xl leading-tight font-bold">Built for enterprise-grade sales workflows.</h2>
                <p className="mb-8 text-brand-muted">
                  Every feature is designed to eliminate friction in the sales cycle while maintaining brand voice
                  and accuracy.
                </p>
                <a
                  href="#features-list"
                  className="inline-flex items-center gap-2 border-b-2 border-brand-primary pb-1 font-semibold text-black transition-all hover:gap-4"
                >
                  View all features
                  <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </a>
              </div>

              <div className="grid gap-8 sm:grid-cols-2 lg:col-span-2">
                {FEATURES.map((feature) => (
                  <div
                    key={feature.title}
                    className="rounded-3xl border border-brand-border bg-white p-8 transition-shadow hover:shadow-lg"
                  >
                    <div className={`mb-6 flex h-12 w-12 items-center justify-center rounded-xl ${feature.tint}`}>
                      <feature.icon className="h-6 w-6" aria-hidden="true" />
                    </div>
                    <h4 className="mb-3 text-xl font-bold">{feature.title}</h4>
                    <p className="text-sm leading-relaxed text-brand-muted">{feature.body}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="py-32">
          <div className="mx-auto max-w-7xl px-6">
            <div className="relative overflow-hidden rounded-[3rem] bg-brand-secondary p-12 text-center text-white md:p-24">
              <div className="relative z-10 mx-auto max-w-3xl">
                <h2 className="mb-8 text-4xl leading-tight font-bold md:text-5xl">
                  Ready to scale your sales without scaling your headcount?
                </h2>
                <p className="mb-12 text-xl text-white/80">
                  See how Knotic can automate your frontline sales conversations.
                </p>
                <div className="flex flex-col justify-center gap-6 sm:flex-row">
                  <a
                    href="#demo"
                    className="rounded-2xl bg-white px-10 py-5 text-lg font-bold text-brand-secondary transition-colors hover:bg-brand-bg"
                  >
                    Schedule a demo
                  </a>
                  <a
                    href="#contact"
                    className="rounded-2xl border border-white/30 px-10 py-5 text-lg font-bold text-white transition-colors hover:bg-white/10"
                  >
                    Contact sales
                  </a>
                </div>
              </div>
              <div className="absolute top-0 right-0 -mt-64 -mr-64 h-[500px] w-[500px] rounded-full bg-white/5 blur-[100px]" />
              <div className="absolute bottom-0 left-0 -mb-64 -ml-64 h-[500px] w-[500px] rounded-full bg-brand-primary/20 blur-[100px]" />
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-brand-border bg-white pt-24 pb-12">
        <div className="mx-auto max-w-7xl px-6">
          <div className="mb-20 grid grid-cols-2 gap-12 md:grid-cols-4 lg:grid-cols-5">
            <div className="col-span-2">
              <div className="mb-6 text-2xl font-bold tracking-tighter">KNOTIC</div>
              <p className="mb-8 max-w-xs text-sm text-brand-muted">
                An AI voice agent that qualifies leads, answers questions, and books meetings for your sales team.
              </p>
            </div>
            <div>
              <h5 className="mb-6 text-sm font-bold tracking-widest uppercase">Product</h5>
              <ul className="space-y-4 text-sm text-brand-muted">
                <li>
                  <a href="#" className="transition-colors hover:text-black">
                    Voice Agent
                  </a>
                </li>
                <li>
                  <a href="#" className="transition-colors hover:text-black">
                    Integrations
                  </a>
                </li>
                <li>
                  <a href="#" className="transition-colors hover:text-black">
                    Security
                  </a>
                </li>
                <li>
                  <a href="#" className="transition-colors hover:text-black">
                    API Docs
                  </a>
                </li>
              </ul>
            </div>
            <div>
              <h5 className="mb-6 text-sm font-bold tracking-widest uppercase">Company</h5>
              <ul className="space-y-4 text-sm text-brand-muted">
                <li>
                  <a href="#" className="transition-colors hover:text-black">
                    About
                  </a>
                </li>
                <li>
                  <a href="#" className="transition-colors hover:text-black">
                    Careers
                  </a>
                </li>
                <li>
                  <a href="#" className="transition-colors hover:text-black">
                    Blog
                  </a>
                </li>
                <li>
                  <a href="#" className="transition-colors hover:text-black">
                    Contact
                  </a>
                </li>
              </ul>
            </div>
            <div>
              <h5 className="mb-6 text-sm font-bold tracking-widest uppercase">Legal</h5>
              <ul className="space-y-4 text-sm text-brand-muted">
                <li>
                  <a href="#" className="transition-colors hover:text-black">
                    Privacy
                  </a>
                </li>
                <li>
                  <a href="#" className="transition-colors hover:text-black">
                    Terms
                  </a>
                </li>
                <li>
                  <a href="#" className="transition-colors hover:text-black">
                    Compliance
                  </a>
                </li>
              </ul>
            </div>
          </div>
          <div className="flex flex-col items-center justify-between gap-4 border-t border-brand-border pt-8 md:flex-row">
            <p className="text-xs text-brand-muted">© 2026 Knotic. All rights reserved.</p>
          </div>
        </div>
      </footer>
    </div>
  );
}
