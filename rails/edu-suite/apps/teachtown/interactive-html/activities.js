// Interactive worksheet activities for TeachTown units. The site calls
//   window.renderActivity(dock, act, onComplete)
// to fill the side panel next to the worksheet image with a purpose-built widget for
// the activity's `kind` (match | drag-drop | highlight | fill-in). Each widget is
// self-contained: it owns its selections, a Check button, and feedback, and calls
// onComplete() once everything is correct. Unknown kinds render nothing (the caller
// keeps its plain type-in dock). Activity data is produced + validated by the builder.
(function () {
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function shuffle(arr) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      const t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }
  // Title + body + a Check button and feedback line, shared by every widget.
  function scaffold(dock, title, hint) {
    dock.classList.add('act');
    dock.appendChild(el('b', 'actTitle', title));
    if (hint) dock.appendChild(el('small', 'actHint', hint));
    const body = el('div', 'actBody');
    dock.appendChild(body);
    const foot = el('div', 'actFoot');
    const btn = el('button', 'actCheck', 'Check my work ★');
    const fb = el('span', 'actFb');
    foot.appendChild(btn); foot.appendChild(fb);
    dock.appendChild(foot);
    return { body, btn, fb };
  }
  function feedback(fb, done, total, correct) {
    fb.textContent = done ? 'Great job! ⭐' : (correct + '/' + total + ' correct — try again');
    fb.className = 'actFb ' + (done ? 'good' : 'bad');
  }
  function finish(btn, fb, correct, total, onComplete) {
    const done = correct === total && total > 0;
    feedback(fb, done, total, correct);
    if (done && onComplete) onComplete();
  }

  // match: each left term gets a dropdown of the shuffled right options.
  function renderMatch(dock, act, onComplete) {
    const pairs = act.pairs || [];
    const rights = shuffle(pairs.map(p => p.right));
    const { body, btn, fb } = scaffold(dock, 'Match them up', 'Pick the match for each one.');
    const rows = [];
    pairs.forEach(p => {
      const row = el('div', 'actRow');
      row.appendChild(el('span', 'actLeft', p.left));
      const sel = el('select', 'actSelect');
      sel.appendChild(new Option('— choose —', ''));
      rights.forEach(r => sel.appendChild(new Option(r, r)));
      row.appendChild(sel);
      rows.push({ sel, want: p.right });
      body.appendChild(row);
    });
    btn.onclick = () => {
      let correct = 0;
      rows.forEach(({ sel, want }) => {
        const ok = sel.value === want;
        sel.className = 'actSelect ' + (sel.value ? (ok ? 'ok' : 'no') : '');
        if (ok) correct++;
      });
      finish(btn, fb, correct, rows.length, onComplete);
    };
  }

  // drag-drop: drag item chips into labeled target slots (tap-to-place fallback).
  function renderDragDrop(dock, act, onComplete) {
    const items = act.items || [], targets = act.targets || [];
    const { body, btn, fb } = scaffold(dock, 'Drag into place', 'Drag each card to its box (or tap a card, then a box).');
    const chips = {};
    let picked = null;
    const bank = el('div', 'actBank');
    shuffle(items).forEach(it => {
      const chip = el('button', 'actChip', it);
      chip.draggable = true;
      chip.ondragstart = e => e.dataTransfer.setData('text', it);
      chip.onclick = () => {
        picked = it;
        Object.values(chips).forEach(c => c.classList.remove('sel'));
        chip.classList.add('sel');
      };
      chips[it] = chip; bank.appendChild(chip);
    });
    body.appendChild(bank);
    const slots = [];
    targets.forEach(t => {
      const slot = el('div', 'actSlot');
      slot.appendChild(el('span', 'actSlotLabel', t.label));
      const zone = el('div', 'actZone', 'drop here');
      const place = it => {
        if (!it) return;
        zone.textContent = it; zone.dataset.item = it; zone.classList.add('filled');
        picked = null;
        Object.values(chips).forEach(c => c.classList.remove('sel'));
      };
      zone.ondragover = e => e.preventDefault();
      zone.ondrop = e => { e.preventDefault(); place(e.dataTransfer.getData('text')); };
      zone.onclick = () => place(picked);
      slot.appendChild(zone);
      slots.push({ zone, want: t.answer });
      body.appendChild(slot);
    });
    btn.onclick = () => {
      let correct = 0;
      slots.forEach(({ zone, want }) => {
        const has = !!zone.dataset.item, ok = zone.dataset.item === want;
        zone.classList.toggle('ok', has && ok);
        zone.classList.toggle('no', has && !ok);
        if (ok) correct++;
      });
      finish(btn, fb, correct, slots.length, onComplete);
    };
  }

  // highlight: tap the correct option per question; it stays lit.
  function renderHighlight(dock, act, onComplete) {
    const qs = act.questions || [];
    const { body, btn, fb } = scaffold(dock, 'Highlight the answer', 'Tap the correct choice for each question.');
    const picks = [];
    qs.forEach((q, qi) => {
      const block = el('div', 'actQ');
      block.appendChild(el('div', 'actQPrompt', (qi + 1) + '. ' + q.prompt));
      const opts = el('div', 'actOpts');
      const pick = { chosen: null };
      shuffle(q.options).forEach(o => {
        const b = el('button', 'actOpt', o);
        b.onclick = () => {
          pick.chosen = o;
          opts.querySelectorAll('.actOpt').forEach(x => x.classList.remove('lit'));
          b.classList.add('lit');
        };
        opts.appendChild(b);
      });
      block.appendChild(opts);
      picks.push({ pick, want: q.answer, opts });
      body.appendChild(block);
    });
    btn.onclick = () => {
      let correct = 0;
      picks.forEach(({ pick, want, opts }) => {
        opts.querySelectorAll('.actOpt').forEach(x => {
          x.classList.toggle('ok', x.textContent === want);
          x.classList.toggle('no', x.classList.contains('lit') && x.textContent !== want);
        });
        if (pick.chosen === want) correct++;
      });
      finish(btn, fb, correct, picks.length, onComplete);
    };
  }

  // fill-in: type an answer per question; graded against the key (open-ended if none).
  function renderFillin(dock, act, onComplete) {
    const qs = act.questions || [];
    const { body, btn, fb } = scaffold(dock, 'Type your answers', 'Answer each question.');
    const norm = s => s.trim().toLowerCase().replace(/[.!?]+$/, '');
    const rows = [];
    qs.forEach((q, qi) => {
      const row = el('div', 'actFill');
      row.appendChild(el('label', 'actFillQ', (qi + 1) + '. ' + q.prompt));
      const input = el('input', 'actFillInput');
      input.placeholder = 'Type answer…';
      row.appendChild(input);
      rows.push({ input, want: (q.answer || '').trim() });
      body.appendChild(row);
    });
    btn.onclick = () => {
      let correct = 0;
      rows.forEach(({ input, want }) => {
        const val = input.value.trim();
        const ok = want ? norm(val) === norm(want) : !!val;  // open-ended: any answer counts
        input.className = 'actFillInput ' + (val ? (ok ? 'ok' : 'no') : '');
        if (ok) correct++;
      });
      finish(btn, fb, correct, rows.length, onComplete);
    };
  }

  const RENDERERS = {
    'match': renderMatch, 'drag-drop': renderDragDrop,
    'highlight': renderHighlight, 'fill-in': renderFillin,
  };
  window.renderActivity = function (dock, act, onComplete) {
    const fn = act && RENDERERS[act.kind];
    if (fn) fn(dock, act, onComplete);
    return !!fn;
  };
})();
