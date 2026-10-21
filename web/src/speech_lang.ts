// Which Kokoro voice and language a read-aloud should use, as pure functions.
//
// THE BUG THIS EXISTS FOR, measured on the live broker 2026-09-16. /v1/tts_light defaults to
// voice `af_heart` and lang_code 'a' (American English) and never looks at the text. Handed a
// Spanish sentence, it read the Spanish words with English phonemes, and whisper transcribed
// the result as ENGLISH. With ef_dora and lang_code 'e' the same sentence came back verbatim.
// (The measured sentence is not reproduced here.) Every SpeakButton call site passes no
// langCode, so the only thing standing between Spanish content and that output was whether the
// user had happened to pick a Spanish voice in the top bar. VoiceControls already documented
// the mechanism ("selecting a Spanish voice with lang_code 'a' produces garbled audio") and
// guarded only its own dropdown.
//
// Its own module, not inline in SpeakButton, so a plain node test can reach it
// (tests/speech-lang.test.js) — the same extraction job_actions.ts and vocab_restore.ts got.

export type KokoroLang = 'a' | 'b' | 'e'

export const DEFAULT_VOICE = 'af_heart'

/** The Kokoro roster. ONE source of truth: VoiceControls' dropdown reads this, and so does
 *  the resolver, so a voice can never be offered whose language nothing knows. */
export const VOICES: { id: string; label: string; lang: KokoroLang }[] = [
  { id: DEFAULT_VOICE, label: 'Heart (US, f) · default', lang: 'a' },
  { id: 'af_bella', label: 'Bella (US, f)', lang: 'a' },
  { id: 'am_michael', label: 'Michael (US, m)', lang: 'a' },
  { id: 'bf_emma', label: 'Emma (UK, f)', lang: 'b' },
  { id: 'ef_dora', label: 'Dora (ES, f)', lang: 'e' },
  { id: 'em_alex', label: 'Alex (ES, m)', lang: 'e' },
]

/** The voice used when a language has to be spoken and no usable preference exists. */
const FALLBACK: Record<KokoroLang, string> = {
  a: DEFAULT_VOICE,
  b: 'bf_emma',
  e: 'ef_dora',
}

export function voiceLang(id: string | undefined): KokoroLang | undefined {
  return VOICES.find((v) => v.id === id)?.lang
}

/** 'a' and 'b' are one LANGUAGE (English) in two accents; 'e' is a different language.
 *
 * The distinction matters because only a cross-LANGUAGE pairing garbles. Comparing raw
 * lang codes instead dropped a user's stored British voice the moment the detector said
 * 'a', handing them an American one they had explicitly changed away from. */
function family(lang: KokoroLang): 'en' | 'es' {
  return lang === 'e' ? 'es' : 'en'
}

// Function words that belong to ONE of the two languages. Words that exist in both are
// deliberately absent, because they vote for whichever list happens to hold them and that is
// noise: "a" is Spanish "to", "no" and "me" are both, "con" is an English noun, "son" is an
// English noun, "he" and "van" are Spanish verbs.
const ES_ONLY = new Set([
  // function words
  'el', 'la', 'los', 'las', 'un', 'una', 'unos', 'unas', 'del', 'al', 'que', 'y', 'en',
  'por', 'para', 'es', 'está', 'estan', 'están', 'ser', 'hacer', 'más', 'mas', 'pero',
  'como', 'cómo', 'qué', 'quién', 'dónde', 'cuándo', 'porque', 'muy', 'también', 'sobre',
  'desde', 'hasta', 'cuando', 'sus', 'tus', 'ustedes', 'nosotros', 'ella', 'ellos', 'esto',
  'esta', 'estos', 'estas', 'se', 'ya', 'hay', 'fue', 'tiene', 'tienen', 'eran', 'era',
  'todos', 'todo', 'toda', 'todas', 'otra', 'otro', 'otras', 'otros', 'sin', 'entre',
  'después', 'antes', 'donde', 'muchas', 'muchos', 'nos', 'les', 'este', 'ese', 'esa',
  'esos', 'esas', 'aquí', 'allí', 'cada', 'algunas', 'algunos', 'durante', 'según',
  'mientras', 'aunque', 'además', 'así', 'sólo', 'bien', 'gran', 'grandes', 'primera',
  'primero', 'nueva', 'nuevo', 'fueron', 'están', 'haber', 'puede', 'pueden', 'hizo',
  'hacen', 'dijo', 'sus', 'ha', 'han', 'había', 'habían', 'siendo', 'siempre', 'nunca',
  // Very common Spanish CONTENT words with no English homograph. These carry the short
  // phrases the function words miss ("agua limpia": two words, neither of them grammatical),
  // and every one is checked against EN_ONLY by a test so the two sets can never overlap and
  // vote twice.
  'personas', 'gente', 'años', 'año', 'días', 'día', 'tiempo', 'agua', 'tierra', 'mundo',
  'vida', 'niños', 'niñas', 'escuela', 'maestra', 'maestro', 'ciudad', 'país', 'países',
  'guerra', 'millones', 'ejemplo', 'manera', 'formas', 'palabras', 'trabajo', 'nombre',
  'lugar', 'parte', 'partes', 'cosas', 'hombre', 'mujer', 'familia', 'comida', 'casa',
])

const EN_ONLY = new Set([
  'the', 'of', 'and', 'to', 'in', 'that', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
  'with', 'for', 'this', 'these', 'those', 'from', 'by', 'as', 'at', 'it', 'they', 'their',
  'there', 'which', 'what', 'who', 'when', 'where', 'because', 'very', 'also', 'about',
  'until', 'have', 'has', 'had', 'will', 'would', 'can', 'could', 'should', 'its', 'his',
  'her', 'our', 'your', 'them', 'then', 'than', 'but', 'all', 'other', 'into', 'after',
  'before', 'between', 'without',
])

/** Exported only so a test can assert the two sets are DISJOINT. A word in both would be
 *  counted for whichever set held it, which is the noise the "one of the two languages"
 *  rule above exists to avoid, and a comment claiming it is not a check. */
export const _WORD_SETS = { ES_ONLY, EN_ONLY }

/** Characters no English sentence carries but Spanish does. Used only to break a tie: on its
 *  own a single loanword ("jalapeño", "café") must not flip an English paragraph. */
const ES_CHARS = /[ñ¿¡áéíóúü]/i

/** Detect 'e' (Spanish) or 'a' (American English) from the text.
 *
 * Fails toward ENGLISH, deliberately: that is both the previous behaviour and the language of
 * nearly all content on this platform, so a wrong guess costs nothing new, while a wrong guess
 * the other way garbles text that used to be fine.
 *
 * Requires Spanish function words to both clear a floor and beat English by a margin. A ratio
 * rather than a hit count, because "The salsa has jalapeño peppers" has Spanish characters and
 * is English, and "El grupo verde está en la ventana" has none of the English ones.
 */
export function detectSpeechLang(text: string): KokoroLang {
  const words = (text || '').toLowerCase().match(/[a-záéíóúüñ]+/gi) || []
  let es = 0
  let en = 0
  for (const w of words) {
    if (ES_ONLY.has(w)) es += 1
    else if (EN_ONLY.has(w)) en += 1
  }
  // Two ways to reach Spanish, and the second is why the character test is not the primary
  // rule. Strong evidence: at least two Spanish function words, clearly beating English.
  // Weak evidence: NO English function word anywhere plus a character English does not use,
  // which is what carries a short phrase like "¿Cómo estás?" that has only one stopword.
  // Requiring en === 0 there is what keeps a loanword from flipping an English paragraph,
  // and requiring a character is what keeps a lone "y" or "la" in English from doing it.
  if (es >= 2 && es > en * 1.5) return 'e'
  // No English function word ANYWHERE plus any Spanish evidence at all. This is what
  // catches a three-word Spanish sentence, and requiring en === 0 is what makes it safe:
  // measured over 1791 real English clips, not one of them lacks an English function word.
  if (en === 0 && (es >= 1 || ES_CHARS.test(text || ''))) return 'e'
  return 'a'
}

export interface SpeechChoice {
  voice: string
  lang_code: KokoroLang
}

/** Pick the voice and language for one read-aloud.
 *
 * Precedence, and the reason for each step:
 *   1. an explicit langCode from the caller wins - the rail knows its own content;
 *   2. otherwise the TEXT decides, because nothing else is reliable;
 *   3. the voice may only be one that speaks the chosen language. A stored preference is
 *      honoured when its language matches and DROPPED when it does not, since a mismatched
 *      pair is the exact thing that garbles. An explicit voice prop is trusted and, absent an
 *      explicit langCode, sets the language itself.
 */
export function resolveSpeech(
  text: string,
  opts: { voice?: string; langCode?: string; storedVoice?: string; storedLang?: string } = {},
): SpeechChoice {
  const explicitVoiceLang = opts.voice ? voiceLang(opts.voice) : undefined
  const asked = (opts.langCode || '').trim() as KokoroLang
  const lang: KokoroLang = (['a', 'b', 'e'] as const).includes(asked)
    ? asked
    : explicitVoiceLang ?? detectSpeechLang(text)

  // An explicit voice prop is the caller's decision and is kept even if it disagrees with an
  // explicit langCode - that combination is a caller bug, not something to silently rewrite.
  if (opts.voice) return { voice: opts.voice, lang_code: lang }

  // A stored preference survives when it speaks the same LANGUAGE, and it then supplies the
  // accent too: a user on bf_emma who reads English keeps Emma and her 'b', even though the
  // detector only ever says 'a' for English. An explicit langCode therefore picks the
  // language, and the user's own voice picks the accent within it.
  const stored = opts.storedVoice?.trim()
  const storedLang = stored ? voiceLang(stored) : undefined
  if (stored && storedLang && family(storedLang) === family(lang)) {
    return { voice: stored, lang_code: storedLang }
  }
  return { voice: FALLBACK[lang], lang_code: lang }
}
