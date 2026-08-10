# Educator Smoke Test — edu-suite dashboard

Thanks for testing! This is a local tool that turns your documents into bilingual
(English / Mexican-Spanish) materials. It runs entirely on the classroom
computer — nothing is sent to the internet. This guide walks you through trying
each of the three tools and tells you what to look for.

No technical knowledge needed. If something doesn't start, see "If it won't
start" at the bottom and ask your setup helper.

---

## 1. Open the dashboard

Double-click **`Open Dashboard.bat`** (in the `apps/dashboard` folder). A small
black window opens (leave it open — it's the engine), and your browser opens to
the dashboard at **http://127.0.0.1:8800**.

You'll see a **New job** box at the top and a **Jobs** list below it.

A few things to know before you start:
- **One job runs at a time.** If you start a second while one is running, it
  waits its turn.
- **The first job of each kind is slow** (a minute or several) because the
  computer is loading the AI models. Later jobs are faster. This is normal — just
  watch the live status.
- **Every finished job gives you a ZIP file** with everything inside (web page,
  audio, images). Open the `index.html` inside it in any browser.

---

## 2. Test 1 — Just Translate

**What it does:** translates a document into Mexican Spanish.

1. Under **Workflow**, choose **Just Translate**.
2. Type a **Name**, e.g. "Water cycle handout".
3. Under **Documents**, choose a short English file (a PDF or a `.txt` with a
   paragraph or two works best for a first test).
4. Click **Start job**.
5. Watch the status appear: **Extract text → Translate to Spanish → Build
   bundle** (you'll see a note when the language model loads).
6. When it says **done**, click **zip** in the Jobs row, open the folder, and
   open `index.html`.

**What to check:**
- [ ] English and Spanish appear side by side.
- [ ] The Spanish reads naturally and simply. Note any lines that sound off.
- [ ] There's also a `.es.txt` file with just the Spanish.

---

## 3. Test 2 — CVC Words (phonics worksheet)

**What it does:** builds a printable phonics worksheet — each word gets a
picture, English + Spanish, audio buttons, and tracing lines.

1. Choose **CVC Words**.
2. Name it, e.g. "Short-a words".
3. Either **upload** a `.txt` with one short word per line (e.g. `cat`, `sun`,
   `web`), **or** check **"Use the sample word set"** to try the built-in words.
4. Click **Start job**.
5. Stages: **Read word list → Translate → Generate images → Generate audio →
   Build worksheet.** You'll see the model change between stages. **This one
   takes several minutes** the first time (three different AI models load).
6. When **done**, open the `zip` → `index.html`.

**What to check:**
- [ ] Each word has a simple cartoon picture that matches its meaning.
- [ ] English word + Spanish word are both shown.
- [ ] The ▶ audio buttons play clear English and Spanish.
- [ ] Use your browser's **Print preview** — the worksheet should lay out cleanly
      on paper.

---

## 4. Test 3 — TeachTown Builder (make a lesson from your own worksheets)

**What it does:** you upload a unit's worksheets and the tool *drafts* a full
interactive lesson (vocabulary + activities) with AI. You review and fix the
draft in a form, then build it — optionally bilingual.

1. Choose **TeachTown Builder**. Name it (e.g. "Weather Unit").
2. Upload the unit's worksheet PDFs. Naming them with the week and subject helps
   the tool sort them (e.g. `Week 1 Science.pdf`, `Week 2 Math.pdf`).
3. Optional: check **"Review the draft before building"** to stop after drafting.
4. Click **Start job.** The AI reads each worksheet and drafts vocabulary and an
   activity (takes a minute or a few).
5. In the Jobs list, click **edit** on the job. Review and fix the drafted
   vocabulary and activities in the form. Check **Add Spanish + audio** if you
   want it bilingual, then click **Build unit.**
6. Open the resulting `zip` → `index.html` — your new interactive unit.

**What to check (this is the newest, roughest feature — your feedback matters most):**
- [ ] Do the drafted vocabulary and activities actually relate to your worksheets?
- [ ] Does editing in the form work, and do your changes show up in the built unit?
- [ ] Does the built unit behave like the sample units (missions, vocab, worksheets)?
- [ ] Note anything the AI got wrong or that was confusing to fix.

---

## 5. Managing your work

- The **Jobs** list is searchable (type in the search box) and can be filtered by
  workflow.
- **zip** re-downloads a finished job anytime.
- **delete** removes a job and its files.
- Finished work is stored on disk under `D:\edu-suite-library`, one folder per
  job, so it stays organized even after many jobs.

---

## 6. What to report back

For each test, please jot down:

| Test | Finished OK? | Output correct? | Quality (1–5) | Notes / anything broken or confusing |
|------|--------------|-----------------|---------------|--------------------------------------|
| Just Translate | | | | |
| CVC Words | | | | |
| TeachTown Builder | | | | |

Especially useful: any **wrong or awkward Spanish**, any **picture that doesn't
match the word**, any **unclear audio**, and anything that **confused you** in
the screens.

---

## If it won't start

- Make sure the black engine window is still open.
- The tool needs the AI service (**Ollama**) running and the models installed,
  and the setup step `uv sync --all-packages` done once. If you see an error like
  "Ollama is not reachable" or "model not installed," send it to your setup
  helper — the message says exactly what's missing.
