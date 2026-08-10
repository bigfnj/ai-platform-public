// One-time/idempotent extractor: reads the data object literals out of the
// original index.html and writes them to data.json so the page can fetch the
// content and the Python enrichment (enrich.py) can read the same source.
// Run from anywhere: `node tools/extract-data.js`.
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");

function grab(name) {
  const m = html.match(new RegExp("const " + name + "=([\\s\\S]*?);\\r?\\n"));
  if (!m) throw new Error("literal not found in index.html: " + name);
  // The literals are pure data (no side effects); eval is safe here.
  return eval("(" + m[1] + ")");
}

const meta = grab("meta");
const weekInfo = grab("weekInfo");
const data = grab("data");
const vocabIcons = grab("vocabIcons");

const units = {
  malala: {
    label: "🏫 Middle School — Malala",
    hero: {
      h1: "Be Brave. Use Your Voice.",
      p: "Travel through Malala’s story while you solve, discover, and lead.",
    },
    weekInfo: weekInfo.malala,
    missions: data.malala,
  },
  beats: {
    label: "🎓 High School — Two Different Beats",
    hero: {
      h1: "Find Your Beat. Make an Impact.",
      p: "Explore music, science, history, and shapes with two remarkable musicians.",
    },
    weekInfo: weekInfo.beats,
    missions: data.beats,
  },
};

const out = {
  brand: { title: "✦ TeachTown Adventures", tagline: "Learn • Play • Grow" },
  meta,
  vocabIcons,
  units,
};

fs.writeFileSync(path.join(root, "data.json"), JSON.stringify(out, null, 2));
console.log(
  "wrote data.json — malala missions:", data.malala.length,
  "| beats missions:", data.beats.length,
  "| vocabIcons:", Object.keys(vocabIcons).length,
  "| malala wk1 vocab:", weekInfo.malala[1].v.length
);
