// Which Kokoro voice and language a read-aloud picks.
//
//   node web/tests/speech-lang.test.js
//
// Plain node, like the edu-suite ones next door: no framework, nothing to install.
// src/speech_lang.ts has no runtime imports at all, so node's type stripping loads it
// directly and nothing here touches platformApi or the DOM.
//
// WHY THIS FILE EXISTS. /v1/tts_light defaults to American English and never looks at the
// text, so Spanish read aloud through it came back as English phonemes over Spanish words,
// which whisper transcribes as English. The detector that fixes it is a heuristic, and a
// heuristic without an accuracy number is a guess.
//
// THE CORPUS HERE IS AUTHORED, AND THE REAL MEASUREMENT IS NOT IN THIS REPO. The detector
// was developed against 3496 real bilingual clips from the edu-suite library, each labelled
// by the key it was stored under, and scored english 1790/1791 = 99.9%, spanish
// 1598/1705 = 93.7% (2026-09-16). That corpus is NOT committed: it is a third-party
// publisher's curriculum text, `web/` is copied wholesale into the public mirror, and
// edu-suite is withheld from that mirror precisely so its content stays private — so
// committing it here would have leaked through the one tree nobody would think to check.
// The 60 clips in fixtures/ are written for this test and modelled on the shapes the real
// measurement exposed: the rail's median clip is 49 characters, its p95 is 115, and the
// cases that actually fail are short fragments, glossary lines and credit lines.
//
// So the floor below is a REGRESSION guard over realistic shapes, not a reproduction of the
// 99.9% figure. The unit tests above it are what pin the individual decisions, including the
// two the real corpus could not catch: a loanword inside English text, and Spanish articles
// quoted in an English sentence.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

const [maj, min] = process.versions.node.split('.').map(Number);
if (maj < 22 || (maj === 22 && min < 18) || (maj === 23 && min < 6)) {
  console.error(`node ${process.versions.node} cannot import a .ts module: type stripping ` +
                'landed in 22.18 and 23.6. Upgrade node — this test has not run.');
  process.exit(1);
}
const { detectSpeechLang, resolveSpeech, voiceLang, VOICES, DEFAULT_VOICE, _WORD_SETS } =
  await import(new URL('../src/speech_lang.ts', import.meta.url).href);

let passed = 0;
function check(name, fn) {
  fn();
  passed += 1;
  console.log(`  ok  ${name}`);
}

console.log('speech-lang: the voice and language a read-aloud picks');

const ES = 'El grupo verde está sentado junto a la ventana. Vamos a buscar ideas para ahorrar agua en la escuela.';
const EN = 'The green group is sitting by the window. Let us look for ideas to save water at school.';

check('Spanish text detects as Spanish', () => {
  assert.equal(detectSpeechLang(ES), 'e');
});

check('English text detects as English', () => {
  assert.equal(detectSpeechLang(EN), 'a');
});

check('a Spanish loanword does NOT flip English text', () => {
  // The reason the decision is a stopword ratio and not a character test. ñ and the accented
  // vowels only break a tie; on their own they must not garble an English paragraph.
  assert.equal(detectSpeechLang('The salsa has jalapeño peppers and a café au lait.'), 'a');
  assert.equal(detectSpeechLang('Señor Vasquez will present the piñata to the class.'), 'a');
});

check('Spanish words QUOTED inside English do not flip it', () => {
  // The margin, not just the floor of two. An English lesson about Spanish articles carries
  // several Spanish function words and is still English; requiring es to beat en by half
  // again is what keeps it English. The corpus cannot catch this, because no real English
  // clip in it happens to contain two words from the Spanish list.
  assert.equal(
    detectSpeechLang('The teacher wrote el and la on the board for the class to copy.'), 'a');
  assert.equal(
    detectSpeechLang('In Spanish, "que" and "por" are very common words that students learn.'),
    'a');
});

check('a short Spanish phrase with no stopwords still detects via its characters', () => {
  assert.equal(detectSpeechLang('¿Cómo estás?'), 'e');
});

check('empty and whitespace fall back to English rather than throwing', () => {
  for (const t of ['', '   ', '\n', '1234', '...']) {
    assert.equal(detectSpeechLang(t), 'a', JSON.stringify(t));
  }
});

check('an explicit langCode beats the text', () => {
  // The rail knows its own content; the detector is only for callers that do not say.
  assert.equal(resolveSpeech(EN, { langCode: 'e' }).lang_code, 'e');
  assert.equal(resolveSpeech(ES, { langCode: 'a' }).lang_code, 'a');
});

check('a junk langCode is ignored and the text decides', () => {
  assert.equal(resolveSpeech(ES, { langCode: 'zz' }).lang_code, 'e');
  assert.equal(resolveSpeech(ES, { langCode: '' }).lang_code, 'e');
});

check('Spanish text with no preference gets a SPANISH voice', () => {
  assert.deepEqual(resolveSpeech(ES), { voice: 'ef_dora', lang_code: 'e' });
});

check('English text with no preference gets the default voice', () => {
  assert.deepEqual(resolveSpeech(EN), { voice: DEFAULT_VOICE, lang_code: 'a' });
});

check('a stored ENGLISH voice is DROPPED for Spanish text', () => {
  // The whole bug, in one assertion. Honouring af_heart here is what produced the garbled,
  // English-phoneme reading: a mismatched voice/lang pair is documented to garble, so the
  // preference loses to the content.
  assert.deepEqual(resolveSpeech(ES, { storedVoice: 'af_heart', storedLang: 'a' }),
                   { voice: 'ef_dora', lang_code: 'e' });
});

check('a stored SPANISH voice is DROPPED for English text', () => {
  // The inverse, which is just as bad and is reachable today: the top-bar choice is sticky
  // per browser, so picking Dora once would otherwise run every English page through the
  // Spanish phonemizer for good.
  assert.deepEqual(resolveSpeech(EN, { storedVoice: 'ef_dora', storedLang: 'e' }),
                   { voice: DEFAULT_VOICE, lang_code: 'a' });
});

check('a stored voice that MATCHES the text is kept', () => {
  // The preference has to still mean something, or this is just an override.
  assert.deepEqual(resolveSpeech(EN, { storedVoice: 'am_michael', storedLang: 'a' }),
                   { voice: 'am_michael', lang_code: 'a' });
  assert.deepEqual(resolveSpeech(ES, { storedVoice: 'em_alex', storedLang: 'e' }),
                   { voice: 'em_alex', lang_code: 'e' });
});

check('an unknown stored voice is dropped rather than sent', () => {
  assert.deepEqual(resolveSpeech(EN, { storedVoice: 'zz_nobody', storedLang: 'a' }),
                   { voice: DEFAULT_VOICE, lang_code: 'a' });
});

check('an explicit voice prop sets the language when no langCode is given', () => {
  assert.deepEqual(resolveSpeech(EN, { voice: 'ef_dora' }),
                   { voice: 'ef_dora', lang_code: 'e' });
});

check('British stays British and is not collapsed into American', () => {
  // The detector only ever says 'a' for English, so comparing raw lang codes dropped a
  // stored British voice and handed back the American one the user had changed away from.
  // 'a' and 'b' are one language in two accents; only a cross-LANGUAGE pair garbles.
  assert.deepEqual(resolveSpeech(EN, { storedVoice: 'bf_emma', storedLang: 'b' }),
                   { voice: 'bf_emma', lang_code: 'b' });
  assert.equal(resolveSpeech(EN, { langCode: 'b' }).lang_code, 'b');
});

check('an explicit English langCode keeps the user\'s accent within English', () => {
  // Pinned because it is a decision, not a side effect: the caller's langCode chooses the
  // LANGUAGE, and the user's own voice chooses the accent inside it. So asking for 'a' with
  // Emma stored returns 'b', deliberately.
  assert.deepEqual(resolveSpeech(EN, { langCode: 'a', storedVoice: 'bf_emma' }),
                   { voice: 'bf_emma', lang_code: 'b' });
  // But a cross-language ask still overrides the preference entirely.
  assert.deepEqual(resolveSpeech(EN, { langCode: 'e', storedVoice: 'bf_emma' }),
                   { voice: 'ef_dora', lang_code: 'e' });
});

check('the two word lists are DISJOINT', () => {
  // A word in both votes for whichever set holds it, which is exactly the noise the lists
  // are built to avoid. "a" is Spanish "to", "no" and "me" are both, "con" and "son" are
  // English nouns, "he" and "van" are Spanish verbs — all of them are absent by design, and
  // this is the check that keeps them absent when someone extends a list.
  const both = [..._WORD_SETS.ES_ONLY].filter((w) => _WORD_SETS.EN_ONLY.has(w));
  assert.deepEqual(both, [], `words in both lists: ${both.join(', ')}`);
  assert.ok(_WORD_SETS.ES_ONLY.size > 50 && _WORD_SETS.EN_ONLY.size > 50,
            'a list that shrank to nothing would make this pass vacuously');
});

check('every voice in the roster has a known language', () => {
  // The resolver drops any voice whose language it cannot look up, so a roster entry the
  // table does not cover would be silently unusable.
  for (const v of VOICES) {
    assert.ok(['a', 'b', 'e'].includes(v.lang), v.id);
    assert.equal(voiceLang(v.id), v.lang);
  }
  assert.equal(voiceLang('nope'), undefined);
});

check('every language in the roster has a fallback voice that speaks it', () => {
  for (const lang of new Set(VOICES.map((v) => v.lang))) {
    const got = resolveSpeech('', { langCode: lang });
    assert.equal(got.lang_code, lang, lang);
    assert.equal(voiceLang(got.voice), lang, `fallback for ${lang} is ${got.voice}`);
  }
});

// --- accuracy over the authored corpus ---------------------------------------------------
const CORPUS = path.join(HERE, 'fixtures', 'speech-lang-corpus.json');
if (!fs.existsSync(CORPUS)) {
  console.error(`\nMISSING FIXTURE ${CORPUS}\n` +
                'The accuracy floor is the only check that spans more than the hand-picked\n' +
                'cases above. Refusing to report a pass without it.');
  process.exit(1);
}
check('accuracy over the corpus holds its floor', () => {
  const rows = JSON.parse(fs.readFileSync(CORPUS, 'utf8'));
  assert.ok(rows.length >= 60, `corpus shrank to ${rows.length}`);
  // Both halves non-degenerate: an all-English corpus would pass the English floor while
  // saying nothing at all about the Spanish side, which is the side that was broken.
  for (const lang of ['en', 'es']) {
    assert.ok(rows.filter((r) => r.lang === lang).length >= 25,
              `only ${rows.filter((r) => r.lang === lang).length} ${lang} clips`);
  }
  const tally = { en: { n: 0, ok: 0 }, es: { n: 0, ok: 0 } };
  const misses = { en: [], es: [] };
  for (const r of rows) {
    const want = r.lang === 'es' ? 'e' : 'a';
    const got = detectSpeechLang(r.text);
    tally[r.lang].n += 1;
    if (got === want) tally[r.lang].ok += 1;
    else if (misses[r.lang].length < 3) misses[r.lang].push(r.text.slice(0, 90));
  }
  const pct = (t) => (100 * t.ok) / t.n;
  console.log(`        english ${tally.en.ok}/${tally.en.n} = ${pct(tally.en).toFixed(1)}%`);
  console.log(`        spanish ${tally.es.ok}/${tally.es.n} = ${pct(tally.es).toFixed(1)}%`);
  for (const k of ['en', 'es']) {
    for (const m of misses[k]) console.log(`        missed ${k}: ${JSON.stringify(m)}`);
  }
  // English is the floor that matters most: a false Spanish reading garbles text that works
  // today, so it is held at 100% over this corpus. Spanish is allowed a couple of misses,
  // and the fixture deliberately includes the two shapes that do miss ("cobre o bronce",
  // "de vidrio") rather than only sentences the detector handles - a corpus built from
  // passing cases would hold any floor you asked of it.
  assert.ok(pct(tally.en) >= 100, `english accuracy fell to ${pct(tally.en).toFixed(1)}%`);
  assert.ok(pct(tally.es) >= 90, `spanish accuracy fell to ${pct(tally.es).toFixed(1)}%`);
});

console.log(`\n${passed} passed`);
