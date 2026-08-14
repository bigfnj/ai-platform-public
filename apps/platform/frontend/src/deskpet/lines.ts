// The pet's "brain" — scripted, offline, no model, no network.
//
// Each rail has its own bank of 100+ curated quips (deskpet/quips/<rail>.json). The shell
// hands us the active rail (rail-awareness), and pickIdle also checks a visible on-screen
// heading against a few keyword triggers (shallow content-awareness, no embeddings). To grow
// or retune a rail's voice, edit that rail's JSON — nothing else depends on the wording.
//
// Convention: when a NEW rail is published, generate a bank of 100+ quips for it (a frontier
// "quip pass") and add it to RAIL below, so the pet is rail-aware everywhere by default.

import recipeBook from "./quips/recipe-book.json";
import eduSuite from "./quips/edu-suite.json";
import iep from "./quips/iep.json";
import workstation from "./quips/workstation.json";
import terminalFun from "./quips/terminal-fun.json";
import aiPlayground from "./quips/ai-playground.json";
import admin from "./quips/admin.json";
import generic from "./quips/generic.json";
import welcome from "./quips/welcome.json";

const RAIL: Record<string, string[]> = {
  "recipe-book": recipeBook,
  "edu-suite": eduSuite,
  iep,
  workstation,
  "terminal-fun": terminalFun,
  "ai-playground": aiPlayground,
  admin,
};

// Content-awareness without a model: if the visible heading matches, comment on it.
const KEYWORDS: { re: RegExp; line: (m: string) => string }[] = [
  { re: /chicken/i, line: () => "Chicken again? Bold." },
  { re: /taco|burrito|nacho/i, line: () => "Taco something? Yes please." },
  { re: /cocktail|martini|whiskey|whisky|gin|vodka|rum/i, line: () => "A drink? Make it a double." },
  { re: /rose|tulip|peony|lily|orchid/i, line: (m) => `${m.toLowerCase()}s — good taste.` },
  { re: /budget|loan|mortgage|invoice|paystub/i, line: () => "Numbers. Deep breath." },
  { re: /goal|present level/i, line: () => "Measurable and specific — you know the drill." },
  { re: /\brag\b|embedding|cosine|retrieval|nvidia|\bnim\b/i, line: () => "Grounded answers only — cite the sources." },
];

function pick(arr: string[]): string {
  return arr[Math.floor(Math.random() * arr.length)];
}

export function pickArrive(rail: string, label: string): string {
  const bank = RAIL[rail];
  if (bank && bank.length) return pick(bank);
  return `Welcome to ${label || "the platform"}.`;
}

// One-time, per-login personalized welcome. Each line has a single {name} slot.
export function pickWelcome(name: string): string {
  return pick(welcome).replace(/\{name\}/g, name || "friend");
}

export function pickIdle(rail: string, snippet: string): string {
  if (snippet) {
    for (const k of KEYWORDS) {
      const m = snippet.match(k.re);
      if (m) return k.line(m[0]);
    }
  }
  const bank = RAIL[rail];
  const pool = bank && bank.length ? [...bank, ...generic] : generic;
  return pick(pool);
}
