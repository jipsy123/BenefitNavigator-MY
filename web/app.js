/* BenefitNavigator Malaysia — vanilla JS UI, no build step.
 *
 * Language model: the server reasons/verifies in Malay and returns a verified
 * `canonical_ms` payload plus a `result` already localized to the requested lang.
 * The client caches the Malay canonical and re-localizes toggles via /localize
 * (always from Malay, never re-running the pipeline). UI chrome is switched
 * instantly via i18n.js (t() / applyChrome). See i18n.js for the string tables.
 */
'use strict';

const langSelect      = document.getElementById('lang-select');
const btnTextSize     = document.getElementById('btn-text-size');
const btnTheme        = document.getElementById('btn-theme');
const btnThemeLabel   = document.getElementById('btn-theme-label');
const situationInput  = document.getElementById('situation-input');
const btnAssess       = document.getElementById('btn-assess');
const srStatus        = document.getElementById('sr-status');
const inlineError     = document.getElementById('inline-error');
const inlineErrorText = document.getElementById('inline-error-text');
const resultsSection  = document.getElementById('results');

const translationNoticeContainer = document.getElementById('translation-notice-container');
const groundednessContainer = document.getElementById('groundedness-container');
const refusalContainer      = document.getElementById('refusal-container');
const totalBannerContainer  = document.getElementById('total-banner-container');
const pipelineContainer     = document.getElementById('pipeline-container');
const messageContainer      = document.getElementById('message-container');
const assumptionsContainer  = document.getElementById('assumptions-container');
const eligibleContainer     = document.getElementById('eligible-container');
const gapsContainer         = document.getElementById('gaps-container');
const nextstepsContainer    = document.getElementById('nextsteps-container');
const citationsContainer    = document.getElementById('citations-container');

const grillPanel       = document.getElementById('grill');
const grillProgressEl  = document.getElementById('grill-progress');
const grillPresumedEl  = document.getElementById('grill-presumed');
const grillTranscriptEl = document.getElementById('grill-transcript');
const grillCurrentEl   = document.getElementById('grill-current');
const intakeCard       = document.querySelector('.intake-card');

// Live agent-activity stream (SSE from /chat/stream).
const agentStreamEl    = document.getElementById('agent-stream');
const agentStreamLog   = document.getElementById('agent-stream-log');
const agentStreamLive  = document.getElementById('agent-stream-live');
// Streaming needs fetch + a readable response body. Older browsers fall back to /chat.
const STREAM_SUPPORTED = typeof window.ReadableStream !== 'undefined' &&
  typeof TextDecoder !== 'undefined';

let currentLang        = langSelect ? langSelect.value : 'en';
let lastAssessmentText = '';            // running conversation text, reused for appeals
let canonicalMsResult  = null;          // Malay canonical from the last assessment
let resultCacheByLang  = {};            // lang -> localized result dict
let lastStages         = null;          // pipeline trace from the last assess turn
let lastGroundedness   = null;          // groundedness from the last assess turn's gate

// Conversation state. The signed token (opaque to the client) carries the real
// facts/asked/turn SERVER-SIDE; the client only keeps what it must render, so the
// browser never holds — or can tamper with — the eligibility inputs.
let chatToken         = null;           // signed state token from the previous turn
let grillActive       = false;
let grillCurrent      = null;           // active question {field, answer_kind, …, _reply, _lang}
let grillTranscript   = [];             // [{field, kind, value, skipped}] (local, display only)
let grillLastProgress = null;           // last progress dict (for re-render on toggle)
let grillBusy         = false;          // one in-flight /chat call at a time

// The Malay sentence a structured answer becomes ON THE WIRE. The UI shows the choice
// in the display language; the message sent to /chat is always Malay, so the intake
// agent (which reasons in Malay — canonical_ms is the source of truth) parses it
// reliably and we maintain just one set of templates. Free-text "Other" answers are
// sent verbatim, and double as the way to correct a wrong assumption (a typed hard
// fact overrides any server-side presumption).
const MARITAL_MS = { single: 'bujang', married: 'berkahwin', widowed: 'balu/duda', divorced: 'bercerai' };
const ANSWER_MS = {
  citizen:        v => v ? 'Ya, saya warganegara Malaysia.' : 'Tidak, saya bukan warganegara Malaysia.',
  is_oku:         v => v ? 'Ya, saya seorang OKU (orang kurang upaya).' : 'Tidak, saya bukan OKU.',
  has_kad_oku:    v => v ? 'Ya, saya memegang Kad OKU JKM yang berdaftar.' : 'Tidak, saya tidak memegang Kad OKU.',
  unable_to_work: v => v ? 'Ya, saya langsung tidak berupaya bekerja.' : 'Tidak, saya masih boleh bekerja.',
  is_working:     v => v ? 'Ya, saya sedang bekerja.' : 'Tidak, saya tidak bekerja sekarang.',
  is_carer:       v => v ? 'Ya, saya penjaga sepenuh masa kepada pesakit atau OKU terlantar.' : 'Tidak, saya bukan penjaga sepenuh masa.',
  has_dependents: v => v ? 'Ya, saya mempunyai anak atau tanggungan.' : 'Tidak, saya tiada tanggungan.',
  str_approved:   v => v ? 'Ya, permohonan STR saya telah diluluskan.' : 'Tidak, permohonan STR saya belum diluluskan.',
  ekasih_listed:  v => v ? 'Ya, saya tersenarai dalam pangkalan data eKasih.' : 'Tidak, saya tidak tersenarai dalam eKasih.',
  marital_status: v => 'Status perkahwinan saya ialah ' + (MARITAL_MS[v] || v) + '.',
  age:               v => 'Umur saya ' + v + ' tahun.',
  individual_income: v => 'Pendapatan bulanan saya sendiri ialah RM' + v + '.',
  household_income:  v => 'Jumlah pendapatan bulanan isi rumah saya ialah RM' + v + '.',
};
const SKIP_MS = 'Saya tidak pasti tentang soalan itu dan ingin melangkaunya.';

function answerToMessage(field, value) {
  const fn = ANSWER_MS[field];
  return fn ? fn(value) : String(value);
}

// Store the active question stamped with the phrased reply + its language — on a
// language toggle the static template takes over (the reply isn't re-translated).
function setGrillCurrent(question, reply) {
  grillCurrent = question ? { ...question, _reply: reply || '', _lang: currentLang } : null;
}

// Build element safely (no innerHTML). props: class/text/aria/{attr} keys.
function el(tag, props, children) {
  const node = document.createElement(tag);
  if (props) {
    Object.entries(props).forEach(([k, v]) => {
      if (k === 'class') { node.className = v; }
      else if (k === 'text') { node.textContent = v; }
      else if (k === 'aria') {
        Object.entries(v).forEach(([ak, av]) => node.setAttribute('aria-' + ak, av));
      }
      else { node.setAttribute(k, v); }
    });
  }
  if (children) {
    (Array.isArray(children) ? children : [children]).forEach(ch => {
      if (ch == null) return;
      node.appendChild(typeof ch === 'string' ? document.createTextNode(ch) : ch);
    });
  }
  return node;
}

function clearEl(c) { while (c.firstChild) c.removeChild(c.firstChild); }

// Advance the intake journey rail. Steps before the current one (and ALL steps
// once results are shown) are 'done'; only the current in-progress step is
// 'active' — so nothing looks active after the journey is complete.
const PHASE_ORDER = ['describe', 'questions', 'results'];
function setIntakePhase(phase) {
  if (!intakeCard) return;
  const idx = PHASE_ORDER.indexOf(phase);
  const allDone = phase === 'results';
  intakeCard.querySelectorAll('.rail-step').forEach(step => {
    const sIdx = PHASE_ORDER.indexOf(step.dataset.step);
    const done = allDone || sIdx < idx;
    step.classList.toggle('is-done', done);
    step.classList.toggle('is-active', !done && sIdx === idx);
  });
}

function announce(msg) {
  srStatus.textContent = '';
  setTimeout(() => { srStatus.textContent = msg; }, 50);
}

// The headline figure only — the (often long) note is rendered separately so it
// can wrap instead of overflowing the card. See amountNote().
function formatAmount(amount) {
  if (!amount) return '';
  if (amount.type === 'fixed') {
    return 'RM' + amount.monthly_myr + t('per_month');
  }
  if (amount.type === 'range') {
    return 'RM' + amount.monthly_myr_min + '–RM' + amount.monthly_myr_max + t('per_month');
  }
  return '';
}

// The localized qualifier note attached to an amount, if any (shown on its own line).
function amountNote(amount) {
  return (amount && amount.note_ms) ? amount.note_ms : '';
}

// Append the amount's qualifier note under a card header, when present.
function appendAmountNote(card, program) {
  const note = amountNote(program && program.amount);
  if (note) card.appendChild(el('div', { class: 'amount-note', text: note }));
}

// Append a labelled bullet list to a parent element.
function appendListSection(parent, cls, label, items) {
  if (!items || items.length === 0) return;
  const div = el('div', { class: cls });
  div.appendChild(el('strong', { text: label }));
  const ul = el('ul');
  items.forEach(i => ul.appendChild(el('li', { text: i })));
  div.appendChild(ul);
  parent.appendChild(div);
}

// Safe citation link — validates source_url to block javascript: URIs.
function buildCitationLink(citation) {
  if (!citation) return null;
  const label = t('source_prefix') + (citation.doc_title || '') +
    (citation.locator ? ', ' + citation.locator : '');
  const url = citation.source_url || '';
  const isHttpUrl = /^https?:\/\//i.test(url);
  if (isHttpUrl) {
    return el('a', { href: url, target: '_blank', rel: 'noopener noreferrer', text: label });
  }
  return el('span', { class: 'citation-plain', text: label + (url ? ' (' + url + ')' : '') });
}

function agencyClass(agency) {
  if (!agency) return 'other';
  const upper = agency.toUpperCase();
  if (upper === 'JKM') return 'jkm';
  if (upper === 'LHDN') return 'lhdn';
  return 'other';
}

// ===== Language handling ====================================================

langSelect.addEventListener('change', () => {
  currentLang = langSelect.value;
  applyChrome(currentLang);
  refreshThemeLabel();           // the Dark/Light label is dynamic — re-translate it
  // The grill is field-keyed, so re-render it from i18n — no re-fetch, no mixing.
  if (grillActive) { renderGrill(grillLastProgress); }
  if (canonicalMsResult) { renderLocalized(currentLang); }
});

// Localize the last result into `lang`, from cache or via /localize (from the
// Malay canonical — never re-running the pipeline, never translating a translation).
async function renderLocalized(lang) {
  if (resultCacheByLang[lang]) {
    renderResults(enrichResult(resultCacheByLang[lang]), true);
    return;
  }
  if (lang === 'ms') {
    resultCacheByLang.ms = canonicalMsResult;
    renderResults(enrichResult(canonicalMsResult), true);
    return;
  }
  announce(t('submitting'));
  try {
    const resp = await fetch('/localize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'assess', payload: canonicalMsResult, lang })
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    resultCacheByLang[lang] = data.result;
    renderResults(enrichResult(data.result), data.translation_ok);
  } catch (_err) {
    // Fall back to the verified Malay canonical, visibly.
    renderResults(enrichResult(canonicalMsResult), false);
  }
}

// /chat returns the verdict payload (re-localizable) plus a per-turn trace; the
// pipeline panel + groundedness badge live in module state so they survive language
// toggles (which only re-localize the verdict text). Re-attach them on every render.
function enrichResult(result) {
  if (!result) return result;
  return {
    ...result,
    stages: lastStages || result.stages || [],
    groundedness: lastGroundedness || result.groundedness,
  };
}

btnTextSize.addEventListener('click', () => {
  const isLarge = document.documentElement.classList.toggle('text-large');
  btnTextSize.setAttribute('aria-pressed', String(isLarge));
  announce(isLarge ? t('text_large_on') : t('text_large_off'));
});

// ===== Theme (light / dark) ================================================
// The pre-paint <head> script sets the initial .theme-dark class from
// localStorage or the OS preference; here we only handle the toggle + label.
const THEME_KEY = 'bn-theme';

function isDarkTheme() {
  return document.documentElement.classList.contains('theme-dark');
}

// The label shows the theme you'd switch TO; keep it in sync with state + language.
function refreshThemeLabel() {
  if (btnThemeLabel) btnThemeLabel.textContent = isDarkTheme() ? t('theme_light') : t('theme_dark');
  btnTheme.setAttribute('aria-pressed', String(isDarkTheme()));
}

btnTheme.addEventListener('click', () => {
  const next = isDarkTheme() ? 'light' : 'dark';
  document.documentElement.classList.toggle('theme-dark', next === 'dark');
  try { localStorage.setItem(THEME_KEY, next); } catch (_) { /* storage blocked — session only */ }
  refreshThemeLabel();
  announce(next === 'dark' ? t('theme_dark') : t('theme_light'));
});

document.querySelectorAll('.persona-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const text = btn.getAttribute('data-text');
    if (text) { situationInput.value = text; situationInput.focus(); }
  });
});

function setLoading(loading) {
  btnAssess.disabled = loading;
  btnAssess.setAttribute('aria-busy', String(loading));
  clearEl(btnAssess);
  if (loading) {
    const spinner = el('span', { class: 'spinner', 'aria-hidden': 'true' });
    btnAssess.appendChild(spinner);
    btnAssess.appendChild(document.createTextNode(' ' + t('submitting')));
    announce(t('submitting_announce'));
  } else {
    btnAssess.appendChild(document.createTextNode(t('grill_start_btn')));
  }
}

function showInlineError(msg) {
  inlineErrorText.textContent = msg;
  inlineError.classList.remove('hidden');
  announce(msg);
}
function clearInlineError() {
  inlineError.classList.add('hidden');
  inlineErrorText.textContent = '';
}

// ===== Rendering ============================================================

function renderTranslationNotice(ok) {
  clearEl(translationNoticeContainer);
  if (ok) return;
  const notice = el('div', { class: 'translation-notice', role: 'status' });
  notice.appendChild(el('span', { 'aria-hidden': 'true', text: 'ⓘ ' }));
  notice.appendChild(el('span', { text: t('translation_unavailable') }));
  translationNoticeContainer.appendChild(notice);
}

// "How we checked" — a calm verification log: one row per stage, check + name +
// wrapping summary. Reads as an audit trail rather than a row of loose chips.
function renderPipeline(stages) {
  clearEl(pipelineContainer);
  if (!stages || stages.length === 0) return;
  pipelineContainer.appendChild(el('h2', { text: t('pipeline_heading') }));
  const list = el('ol', { class: 'pipeline-steps', role: 'list' });
  stages.forEach(stage => {
    const status = stage.status || 'ok';
    const isOk = status === 'ok';
    const item = el('li', { class: 'pipeline-step ' + status, role: 'listitem',
      aria: { label: stage.name + ': ' + status + (stage.summary ? '. ' + stage.summary : '') } });
    item.appendChild(el('span', { class: 'pipeline-check', 'aria-hidden': 'true', text: isOk ? '✓' : '✗' }));
    const body = el('div', { class: 'pipeline-step-body' });
    body.appendChild(el('div', { class: 'pipeline-step-name', text: stage.name }));
    if (stage.summary) body.appendChild(el('div', { class: 'pipeline-step-summary', text: stage.summary }));
    item.appendChild(body);
    list.appendChild(item);
  });
  pipelineContainer.appendChild(list);
}

function renderRefusal(messageText) {
  clearEl(refusalContainer);
  const banner = el('div', { class: 'refusal-banner', role: 'alert' });
  banner.appendChild(el('h2', { text: t('refusal_heading') }));
  const msg = el('p', { text: messageText });
  msg.style.whiteSpace = 'pre-wrap';
  banner.appendChild(msg);
  const hotline = el('p', { class: 'hotline' });
  hotline.textContent = t('refusal_hotline_prefix');
  hotline.appendChild(el('a', { href: 'tel:15999', text: '15999' }));
  banner.appendChild(hotline);
  refusalContainer.appendChild(banner);
}

function renderGroundedness(g) {
  clearEl(groundednessContainer);
  if (!g || !g.available) return;
  const isOk = g.grounded;
  const badge = el('div', { class: 'groundedness-badge ' + (isOk ? 'verified' : 'unverified'), role: 'status' });
  badge.appendChild(el('span', { 'aria-hidden': 'true', text: isOk ? '✓ ' : '⚠ ' }));
  badge.appendChild(el('span', { text: isOk ? t('grounded_yes') : t('grounded_no') }));
  if (typeof g.ungrounded_percentage === 'number') {
    badge.appendChild(el('span', { class: 'badge-sub',
      text: ' (' + g.ungrounded_percentage.toFixed(0) + t('grounded_pct_suffix') + ')' }));
  }
  groundednessContainer.appendChild(badge);
}

function renderTotalBanner(totalMin) {
  clearEl(totalBannerContainer);
  if (!totalMin || totalMin <= 0) return;
  const banner = el('div', { class: 'total-banner', role: 'status' });
  banner.appendChild(el('div', { class: 'total-amount',
    text: t('total_prefix') + 'RM' + totalMin + t('per_month') }));
  banner.appendChild(el('div', { class: 'total-label', text: t('total_label') }));
  totalBannerContainer.appendChild(banner);
}

// Render a section body as a bulleted list when it uses "- " item markers
// (the LLM often inlines them), otherwise as a single paragraph.
function renderMessageBody(card, body) {
  if (/^-\s+/.test(body)) {
    const items = body.replace(/^-\s+/, '').split(/\s+-\s+/).map(s => s.trim()).filter(Boolean);
    const ul = el('ul', { class: 'message-list' });
    items.forEach(item => ul.appendChild(el('li', { text: item })));
    card.appendChild(ul);
  } else {
    card.appendChild(el('p', { class: 'message-body', text: body }));
  }
}

function renderMessage(messageText) {
  clearEl(messageContainer);
  if (!messageText) return;
  const card = el('div', { class: 'message-card' });
  // Break the explanation into blocks at numbered markers ("(1) … (2) … (3) …") AND at
  // blank lines, so each section/paragraph is scannable; lift a leading "(n) …:" label
  // into a heading. Single newlines inside a block are preserved by .message-body's
  // white-space: pre-line. Falls back to one block when the text has no such structure.
  const blocks = messageText.split(/\n(?=\s*\(\d+\))|\n{2,}/).map(b => b.trim()).filter(Boolean);
  blocks.forEach(block => {
    const m = block.match(/^(\(\d+\)[^\n:]*:)\s*([\s\S]*)$/);
    if (m) {
      card.appendChild(el('p', { class: 'message-heading', text: m[1].trim() }));
      const body = m[2].trim();
      if (body) renderMessageBody(card, body);
    } else {
      renderMessageBody(card, block);
    }
  });
  messageContainer.appendChild(card);
}

function renderAssumptions(assumptions) {
  clearEl(assumptionsContainer);
  if (!assumptions || assumptions.length === 0) return;
  const card = el('div', { class: 'assumptions-card' });
  card.appendChild(el('h3', { text: t('assumptions_heading') }));
  const ul = el('ul');
  assumptions.forEach(a => ul.appendChild(el('li', { text: a })));
  card.appendChild(ul);
  assumptionsContainer.appendChild(card);
}

// Build a program header div (name + agency tag + amount).
function buildProgramHeader(cls, program) {
  const header = el('div', { class: cls });
  const nameWrap = el('div', { class: 'benefit-name-wrap' });
  nameWrap.appendChild(el('div', { class: 'benefit-name', text: program.name_ms || program.program_id }));
  nameWrap.appendChild(el('span', { class: 'agency-tag ' + agencyClass(program.agency), text: program.agency || '' }));
  header.appendChild(nameWrap);
  const amtStr = formatAmount(program.amount);
  if (amtStr) header.appendChild(el('div', { class: 'benefit-amount', text: amtStr }));
  return header;
}

function appendCitation(card, citation) {
  if (!citation) return;
  const div = el('div', { class: 'benefit-citation' });
  const link = buildCitationLink(citation);
  if (link) div.appendChild(link);
  card.appendChild(div);
}

// Minimum monthly value of a programme — used for the optional amount sort.
function amountValue(program) {
  const a = program && program.amount;
  if (!a) return 0;
  if (a.type === 'fixed') return a.monthly_myr || 0;
  if (a.type === 'range') return a.monthly_myr_min || 0;
  return 0;
}

// The agency a card filters under (real agency string, uppercased; 'OTHER' if none).
function agencyKey(program) {
  return (program.agency || 'OTHER').toUpperCase();
}

function buildEligibleCard(program, order) {
  const card = el('div', { class: 'benefit-card', 'data-agency': agencyKey(program) });
  card._order = order;
  card._amount = amountValue(program);
  card.appendChild(el('span', { class: 'qualify-badge', text: t('you_qualify_badge') }));
  card.appendChild(buildProgramHeader('benefit-card-header', program));
  appendAmountNote(card, program);
  appendCitation(card, program.citation);
  return card;
}

// Agency filter tabs (All + each agency) — only meaningful with 2+ agencies.
function buildFilterTabs(agencies, cards) {
  const tabs = el('div', { class: 'filter-tabs', role: 'group', aria: { label: t('filter_label') } });
  const tabEls = ['ALL', ...agencies].map(key => {
    const tab = el('button', { type: 'button', class: 'filter-chip',
      'aria-pressed': key === 'ALL' ? 'true' : 'false',
      text: key === 'ALL' ? t('filter_all') : key });
    tab.addEventListener('click', () => {
      tabEls.forEach(tEl => tEl.setAttribute('aria-pressed', String(tEl === tab)));
      cards.forEach(card => {
        card.style.display =
          (key === 'ALL' || card.getAttribute('data-agency') === key) ? '' : 'none';
      });
    });
    tabs.appendChild(tab);
    return tab;
  });
  return tabs;
}

// Sort control (best match | highest amount), reordering the cards in place.
function buildSortControl(cards, list) {
  const sortWrap = el('div', { class: 'sort-control' });
  sortWrap.appendChild(el('label', { for: 'eligible-sort', text: t('sort_label') }));
  const select = el('select', { id: 'eligible-sort', class: 'sort-select' });
  select.appendChild(el('option', { value: 'best', text: t('sort_best') }));
  select.appendChild(el('option', { value: 'amount', text: t('sort_amount') }));
  select.addEventListener('change', () => {
    const ordered = [...cards].sort(select.value === 'amount'
      ? (a, b) => b._amount - a._amount
      : (a, b) => a._order - b._order);
    ordered.forEach(card => list.appendChild(card));   // re-append in the new order
  });
  sortWrap.appendChild(select);
  return sortWrap;
}

// Toolbar = filter tabs (2+ agencies) + sort (2+ benefits). Null if neither applies.
function buildEligibleToolbar(agencies, cards, list) {
  const showFilter = agencies.length > 1;
  const showSort = cards.length > 1;
  if (!showFilter && !showSort) return null;
  const toolbar = el('div', { class: 'eligible-toolbar' });
  if (showFilter) toolbar.appendChild(buildFilterTabs(agencies, cards));
  if (showSort) toolbar.appendChild(buildSortControl(cards, list));
  return toolbar;
}

function renderEligible(eligible) {
  clearEl(eligibleContainer);
  if (!eligible || eligible.length === 0) return;
  const section = el('div', { class: 'results-section' });
  section.appendChild(el('h2', { text: t('eligible_heading') + ' (' + eligible.length + ')' }));

  const list = el('div', { class: 'benefit-list' });
  const cards = eligible.map((program, i) => {
    const card = buildEligibleCard(program, i);
    list.appendChild(card);
    return card;
  });

  const agencies = [...new Set(eligible.map(agencyKey))];
  const toolbar = buildEligibleToolbar(agencies, cards, list);
  if (toolbar) section.appendChild(toolbar);
  section.appendChild(list);
  eligibleContainer.appendChild(section);
}

function renderGaps(gaps) {
  clearEl(gapsContainer);
  if (!gaps || gaps.length === 0) return;
  const section = el('div', { class: 'results-section' });
  section.appendChild(el('h2', { text: t('gaps_heading') + ' (' + gaps.length + ')' }));
  gaps.forEach(gap => {
    const card = el('div', { class: 'gap-card' + (gap.near_miss ? ' near-miss' : '') });
    if (gap.program_id) card.id = 'gapcard-' + gap.program_id;   // next-steps deep-link target
    if (gap.near_miss) card.appendChild(el('span', { class: 'near-miss-badge', text: t('near_miss_badge') }));
    card.appendChild(buildProgramHeader('gap-card-header', gap));
    appendAmountNote(card, gap);

    appendListSection(card, 'gap-blocking', t('gap_blocking_label'), gap.blocking_ms);
    appendListSection(card, 'gap-actions', t('gap_actions_label'), gap.actions_ms);
    appendCitation(card, gap.citation);

    const appealRegionId = 'appeal-' + (gap.program_id || Math.random().toString(36).slice(2));
    const btnAppeal = el('button', {
      type: 'button', class: 'btn-appeal', text: t('appeal_btn'),
      'aria-expanded': 'false', 'aria-controls': appealRegionId
    });
    card.appendChild(btnAppeal);

    const appealRegion = el('div', { id: appealRegionId, class: 'appeal-region hidden' });
    card.appendChild(appealRegion);

    btnAppeal.addEventListener('click', () => handleAppeal(gap, btnAppeal, appealRegion));
    section.appendChild(card);
  });
  gapsContainer.appendChild(section);
}

function renderCitations(citations) {
  clearEl(citationsContainer);
  if (!citations || citations.length === 0) return;
  const section = el('div', { class: 'results-section' });
  section.appendChild(el('h2', { text: t('citations_heading') }));
  const ul = el('ul', { class: 'citations-list' });
  citations.forEach((cit, i) => {
    const li = el('li');
    li.appendChild(el('span', { text: (i + 1) + '. ' }));
    const link = buildCitationLink(cit);
    if (link) li.appendChild(link);
    ul.appendChild(li);
  });
  section.appendChild(ul);
  citationsContainer.appendChild(section);
}

// ===== Your next steps (derived from results) ===============================

// Smoothly bring a near-miss gap card into view and flash it.
function scrollToGap(programId) {
  const target = programId && document.getElementById('gapcard-' + programId);
  if (!target) { gapsContainer.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
  target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  target.classList.add('gap-flash');
  setTimeout(() => target.classList.remove('gap-flash'), 1600);
}

function buildNextStep(num, titleText, link, isAppeal) {
  const step = el('li', { class: 'nextstep' + (isAppeal ? ' appeal' : '') });
  step.appendChild(el('span', { class: 'nextstep-num', 'aria-hidden': 'true', text: String(num) }));
  const body = el('div', { class: 'nextstep-body' });
  body.appendChild(el('div', { class: 'nextstep-title', text: titleText }));
  if (link) body.appendChild(link);
  step.appendChild(body);
  return step;
}

// Concrete actions pulled straight from the result: apply for what you qualify
// for, and improve eligibility for near-misses (deep-linking to that gap card).
function renderNextSteps(result) {
  clearEl(nextstepsContainer);
  const eligible = result.eligible || [];
  const nearMiss = (result.gaps || []).filter(g => g.near_miss);
  if (eligible.length === 0 && nearMiss.length === 0) return;

  const section = el('div', { class: 'results-section' });
  section.appendChild(el('h2', { text: t('nextsteps_heading') }));
  const listEl = el('ol', { class: 'nextsteps-list' });
  let n = 0;

  eligible.forEach(program => {
    n += 1;
    const name = program.name_ms || program.program_id || '';
    const agency = program.agency ? ' (' + program.agency + ')' : '';
    const url = program.citation && program.citation.source_url;
    const link = (url && /^https?:\/\//i.test(url))
      ? el('a', { class: 'nextstep-link', href: url, target: '_blank',
          rel: 'noopener noreferrer', text: t('step_source_link') })
      : null;
    listEl.appendChild(buildNextStep(n, t('step_apply', { name }) + agency, link, false));
  });

  nearMiss.forEach(gap => {
    n += 1;
    const name = gap.name_ms || gap.program_id || '';
    const jump = el('button', { type: 'button', class: 'nextstep-link', text: t('step_appeal_link') });
    jump.addEventListener('click', () => scrollToGap(gap.program_id));
    listEl.appendChild(buildNextStep(n, t('step_improve', { name }), jump, true));
  });

  section.appendChild(listEl);
  nextstepsContainer.appendChild(section);
}

// ===== Appeal flow ==========================================================

async function handleAppeal(gap, btnAppeal, appealRegion) {
  const isExpanded = btnAppeal.getAttribute('aria-expanded') === 'true';
  if (isExpanded) {
    btnAppeal.setAttribute('aria-expanded', 'false');
    appealRegion.classList.add('hidden');
    return;
  }
  btnAppeal.setAttribute('aria-expanded', 'true');
  appealRegion.classList.remove('hidden');
  if (appealRegion.dataset.loaded === 'true') return;
  clearEl(appealRegion);
  appealRegion.appendChild(el('div', { class: 'appeal-loading', text: t('appeal_loading') }));
  btnAppeal.disabled = true;
  try {
    const resp = await fetch('/appeal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: lastAssessmentText, program_id: gap.program_id, lang: currentLang })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: t('err_unknown') }));
      throw new Error(err.detail || 'HTTP ' + resp.status);
    }
    const data = await resp.json();
    renderAppealLetter(appealRegion, data.letter, data.translation_ok);
    appealRegion.dataset.loaded = 'true';
  } catch (err) {
    clearEl(appealRegion);
    appealRegion.appendChild(
      el('div', { class: 'appeal-loading', text: t('appeal_fail_prefix') + (err.message || t('err_network')) })
    );
  } finally {
    btnAppeal.disabled = false;
  }
}

function renderAppealLetter(container, letter, translationOk) {
  clearEl(container);

  if (translationOk === false) {
    container.appendChild(el('div', { class: 'translation-notice', role: 'status',
      text: t('translation_unavailable') }));
  }

  const header = el('div', { class: 'appeal-region-header' });
  header.appendChild(el('h4', { text: t('appeal_heading_prefix') + (letter.program_name_ms || letter.program_id || '') }));
  const btnCopy = el('button', { type: 'button', class: 'btn-copy', text: t('copy') });
  header.appendChild(btnCopy);
  container.appendChild(header);

  const body = el('div', { class: 'appeal-body' });
  const letterPre = el('pre', { class: 'appeal-letter' });
  letterPre.textContent = letter.body_ms || '';
  body.appendChild(letterPre);

  if (letter.routing_ms) {
    const routing = el('div', { class: 'appeal-routing' });
    routing.appendChild(el('strong', { text: t('routing_label') }));
    routing.appendChild(document.createTextNode(letter.routing_ms));
    body.appendChild(routing);
  }
  container.appendChild(body);

  btnCopy.addEventListener('click', () => {
    const text = (letter.body_ms || '') +
      (letter.routing_ms ? '\n\n' + t('routing_copy_header') + '\n' + letter.routing_ms : '');
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(() => markCopied(btnCopy)).catch(() => fallbackCopy(text, btnCopy));
    } else {
      fallbackCopy(text, btnCopy);
    }
  });
}

function markCopied(btn) {
  btn.textContent = t('copied');
  btn.classList.add('copied');
  setTimeout(() => { btn.textContent = t('copy'); btn.classList.remove('copied'); }, 2000);
}

function fallbackCopy(text, btn) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand('copy');
    markCopied(btn);
  } catch (_) {
    btn.textContent = t('copy');
  }
  document.body.removeChild(ta);
}

// ===== Grill flow (adaptive interview) ======================================

function clearResultsUI() {
  canonicalMsResult = null;
  resultCacheByLang = {};
  resultsSection.classList.remove('visible');
  [translationNoticeContainer, groundednessContainer, refusalContainer, totalBannerContainer,
   pipelineContainer, messageContainer, assumptionsContainer, eligibleContainer, gapsContainer,
   nextstepsContainer, citationsContainer].forEach(clearEl);
}

// Open the conversation from the free-text paragraph; every turn after is an answer.
btnAssess.addEventListener('click', () => {
  const text = situationInput.value.trim();
  if (!text) {
    showInlineError(t('err_empty'));
    situationInput.focus();
    return;
  }
  clearInlineError();
  clearResultsUI();
  setIntakePhase('describe');          // reset the journey rail for a fresh run
  lastAssessmentText = text;           // opening paragraph; grows with each answer (for appeals)
  chatToken = null;                    // a fresh conversation
  grillActive = true;
  grillCurrent = null; grillTranscript = []; grillLastProgress = null;
  [grillProgressEl, grillPresumedEl, grillTranscriptEl, grillCurrentEl].forEach(clearEl);
  chatTurn(text, { fresh: true });
});

// One conversation turn: POST the message (+ the carried signed token) and route the
// reply. The token is the ONLY thing that crosses turns — the client holds no facts.
async function chatSend(message, opts) {
  if (grillBusy) return;
  grillBusy = true;
  setLoading(true);
  try {
    const resp = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: message,
        token: (opts && opts.fresh) ? null : chatToken,
        lang: currentLang
      })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: t('err_server') }));
      throw new Error(err.detail || 'HTTP ' + resp.status);
    }
    handleTurn(await resp.json());
  } catch (err) {
    showInlineError(t('err_connection') + ' (' + (err.message || '') + ')');
    announce(t('err_connection_announce'));
  } finally {
    grillBusy = false;
    setLoading(false);
  }
}

// One turn, preferring the streamed transport so the user watches the Foundry agents
// work (first output in ~5s) instead of staring at a spinner. Falls back to the JSON
// /chat endpoint on older browsers or if the stream fails before any result.
function chatTurn(message, opts) {
  clearInlineError();   // a new turn supersedes any prior failure banner (it reflects THIS turn)
  return STREAM_SUPPORTED ? chatSendStream(message, opts) : chatSend(message, opts);
}

// ----- Live agent stream (SSE from /chat/stream) -------------------------------

function startAgentStream() {
  clearEl(agentStreamLog);
  agentStreamLive.textContent = '';
  agentStreamLive.setAttribute('aria-hidden', 'true');
  agentStreamEl.classList.remove('hidden');
}

function hideAgentStream() {
  agentStreamEl.classList.add('hidden');
  clearEl(agentStreamLog);
  agentStreamLive.textContent = '';
}

// Upsert one row in the activity log (keyed by id so repeated events update one row).
function streamRow(id, label, state, detail) {
  let row = agentStreamLog.querySelector('[data-row="' + id + '"]');
  if (!row) {
    row = el('li', { class: 'stream-row', role: 'listitem', 'data-row': id });
    row.appendChild(el('span', { class: 'stream-dot', 'aria-hidden': 'true' }));
    row.appendChild(el('span', { class: 'stream-label' }));
    row.appendChild(el('span', { class: 'stream-detail' }));
    agentStreamLog.appendChild(row);
  }
  row.className = 'stream-row is-' + (state || 'run');
  row.querySelector('.stream-label').textContent = label;
  row.querySelector('.stream-detail').textContent =
    detail != null ? detail : (state === 'run' ? t('stream_thinking') : '');
  return row;
}

// Translate one stream event into the live activity log / forming-text region. Every
// row reflects a REAL step: a stage check, an agent running, or an MCP trust-tool call.
function handleStreamEvent(evt) {
  switch (evt.type) {
    case 'stage': {
      const id = 'st-' + evt.stage;
      const label = t('trace_' + String(evt.stage || '').toLowerCase());
      const state = (evt.status === 'error') ? 'err' : 'ok';
      streamRow(id, label, state, '');
      break;
    }
    case 'agent': {
      const id = 'ag-' + evt.agent;
      const label = t('stream_agent_' + evt.agent);
      if (evt.phase === 'start') {
        streamRow(id, label, 'run', '');
        agentStreamLive.textContent = '';                  // its output forms below (visual ticker)
      } else if (evt.phase === 'done') {
        // the Orchestrator's done event carries its real one-line routing reasoning
        streamRow(id, label, 'ok', evt.rationale_ms || '');
      }
      break;
    }
    case 'tool': {                                          // a real MCP trust-tool call
      streamRow('tl-' + evt.agent + '-' + evt.tool,
                '↳ ' + t('stream_tool', { tool: evt.tool }), 'ok', '');
      break;
    }
    case 'reset':
      agentStreamLive.textContent = '';
      break;
    case 'delta':
      // The agents emit Bahasa Melayu. Show the live typewriter only when the display
      // language IS Malay; otherwise the Malay draft would form then be replaced by the
      // localized question (reads as jank). Non-ms users still see the activity log live,
      // and the localized question/result appears on `done`.
      if (currentLang === 'ms') agentStreamLive.textContent += (evt.text || '');
      break;
    default:
      break;
  }
}

// Stream one turn over SSE. Parses `data:` frames, renders progress live, and hands the
// terminal done/error event to handleTurn (same shape as /chat). On a pre-result stream
// failure it falls back to the JSON endpoint once.
async function chatSendStream(message, opts) {
  if (grillBusy) return;
  grillBusy = true;
  setLoading(true);
  startAgentStream();
  let gotTerminal = false;
  try {
    const resp = await fetch('/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: message,
        token: (opts && opts.fresh) ? null : chatToken,
        lang: currentLang
      })
    });
    if (!resp.ok || !resp.body) throw new Error('stream unavailable (HTTP ' + resp.status + ')');
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const dataLine = frame.split('\n').find(l => l.startsWith('data:'));
        if (!dataLine) continue;
        let evt;
        try { evt = JSON.parse(dataLine.slice(5).trim()); } catch (e) { continue; }
        if (evt.type === 'done' || evt.type === 'error') {
          gotTerminal = true;
          hideAgentStream();
          handleTurn(evt);
        } else {
          handleStreamEvent(evt);
        }
      }
    }
    if (!gotTerminal) throw new Error('stream ended without a result');
  } catch (err) {
    hideAgentStream();
    if (!gotTerminal) {                       // never got a result → try the JSON path once
      grillBusy = false;                      // release the lock so chatSend can run
      await chatSend(message, opts);
      return;
    }
    showInlineError(t('err_connection') + ' (' + (err.message || '') + ')');
  } finally {
    grillBusy = false;
    setLoading(false);
  }
}

// Route one /chat turn: ask → next question; assess → verified results; escalate /
// refuse → a calm hand-off to a human; error → a Foundry agent was unreachable
// (fail-hard: no local answer, offer a retry). The verdict gate already ran server-side.
function handleTurn(turn) {
  hideAgentStream();
  if (turn.action === 'error' || turn.type === 'error') {
    grillActive = false;
    showInlineError(t('stream_error'));
    announce(t('stream_error'));
    return;
  }
  chatToken = turn.token || chatToken;

  if (turn.action === 'ask' && turn.question) {
    grillActive = true;
    grillPanel.classList.remove('hidden');
    setIntakePhase('questions');
    grillLastProgress = turn.progress || null;
    setGrillCurrent(turn.question, turn.reply);
    renderGrill(grillLastProgress);
    return;
  }

  if (turn.action === 'assess' && turn.result) {
    grillActive = false;
    setGrillCurrent(null);
    renderGrillProgress(grillLastProgress, true);   // interview complete → full bar, no "still open"
    renderGrillTranscript();
    clearEl(grillCurrentEl);
    grillCurrentEl.appendChild(el('div', { class: 'grill-done', text: t('grill_done') }));
    canonicalMsResult = turn.canonical_ms || null;
    lastStages = traceToStages(turn.trace);
    lastGroundedness = gateGroundedness(turn.trace);
    resultCacheByLang = {};
    if (canonicalMsResult) resultCacheByLang.ms = canonicalMsResult;
    resultCacheByLang[turn.lang] = turn.result;
    renderResults(enrichResult(turn.result), turn.translation_ok);
    return;
  }

  // escalate | refuse | any turn without a usable verdict payload → route to a human.
  grillActive = false;
  canonicalMsResult = null;            // a hand-off message is not a re-localizable verdict
  lastStages = traceToStages(turn.trace);
  lastGroundedness = null;
  renderResults({ refused: true, message_ms: turn.reply || t('refusal_fallback'),
                  stages: lastStages }, turn.translation_ok);
}

// Send the current question's answer — a structured chip, a skip, or free-text "Other".
function answerCurrent(payload) {
  if (!grillCurrent || grillBusy) return;
  const field = grillCurrent.field;
  let message, row;
  if (payload.other != null) {
    message = payload.other;                                   // free text, sent verbatim
    row = { field, kind: 'text', value: payload.other, skipped: false };
  } else if (payload.skip) {
    message = SKIP_MS;
    row = { field, kind: grillCurrent.answer_kind, value: null, skipped: true };
  } else {
    message = answerToMessage(field, payload.value);           // structured → Malay sentence
    row = { field, kind: grillCurrent.answer_kind, value: payload.value, skipped: false };
  }
  grillTranscript = grillTranscript.concat([row]);
  lastAssessmentText += ' ' + message;                         // grow conversation text (appeals)
  chatTurn(message, {});
}

// Map the /chat trace ([{stage,status,…}]) into the "How we checked" panel's shape.
// status: ok/unavailable/fallback all read as success (not failures); the rest are ✗.
const _TRACE_OK = ['ok', 'unavailable', 'fallback'];
function traceToStages(trace) {
  if (!Array.isArray(trace)) return [];
  return trace.map(s => ({
    name: t('trace_' + String(s.stage || '').toLowerCase()),
    status: _TRACE_OK.includes(s.status) ? 'ok' : (s.status || 'ok'),
    summary: ''
  }));
}

// The groundedness verdict lives on the GATE trace entry (or null if unavailable).
function gateGroundedness(trace) {
  if (!Array.isArray(trace)) return null;
  const gate = trace.find(s => s.stage === 'GATE');
  if (!gate || typeof gate.available === 'undefined') return null;
  return { available: gate.available, grounded: gate.grounded,
           ungrounded_percentage: gate.ungrounded_percentage };
}

// ----- Grill rendering (all from i18n + stored state — language-toggle safe) -----

function renderGrill(progress) {
  renderGrillProgress(progress);
  renderGrillTranscript();
  renderGrillCurrent();
}

// `done` = the interview is complete (the engine has enough to decide). When done we show
// a full bar and a completion line — NOT "{undecided} still open", because a programme can
// stay mathematically undecided while no remaining question could change it. Saying "still
// open" there reads as an unanswered question and contradicts "checking your eligibility".
function renderGrillProgress(progress, done) {
  clearEl(grillProgressEl);
  if (!progress) return;
  const pct = done ? 100
    : (progress.total ? Math.round((100 * progress.decided) / progress.total) : 0);
  const bar = el('div', { class: 'grill-progress-bar' + (done ? ' is-done' : ''),
    aria: { hidden: 'true' } });
  const fill = el('div', { class: 'grill-progress-fill' });
  fill.style.width = pct + '%';
  bar.appendChild(fill);
  grillProgressEl.appendChild(bar);
  grillProgressEl.appendChild(el('div', { class: 'grill-progress-text',
    text: done
      ? t('grill_progress_done', { total: progress.total })
      : t('grill_progress', {
          total: progress.total, decided: progress.decided, undecided: progress.undecided
        }) }));
}

function transcriptValueLabel(item) {
  if (item.skipped) return t('grill_skip');
  if (item.kind === 'text') return item.value;          // free-text "Other" answer
  if (item.kind === 'boolean') return item.value ? t('grill_yes') : t('grill_no');
  if (item.kind === 'choice') return t('marital_' + item.value);
  if (item.kind === 'money') return 'RM' + item.value;
  return String(item.value);
}

function renderGrillTranscript() {
  clearEl(grillTranscriptEl);
  grillTranscript.forEach(item => {
    const row = el('div', { class: 'grill-qa', role: 'listitem' });
    row.appendChild(el('span', { class: 'grill-qa-q', text: t('q_' + item.field) }));
    row.appendChild(el('span', { class: 'grill-qa-a' + (item.skipped ? ' skipped' : ''),
      text: transcriptValueLabel(item) }));
    grillTranscriptEl.appendChild(row);
  });
}

function answerBtn(label, onClick, extraClass) {
  const b = el('button', { type: 'button',
    class: 'grill-ans-btn' + (extraClass ? ' ' + extraClass : ''), text: label });
  b.addEventListener('click', onClick);
  return b;
}

function buildAnswerControls(q) {
  const wrap = el('div', { class: 'grill-answers' });
  const primary = el('div', { class: 'grill-primary-answers' });
  const isChip = q.answer_kind === 'boolean' || q.answer_kind === 'choice';

  if (q.answer_kind === 'boolean') {
    primary.appendChild(answerBtn(t('grill_yes'), () => answerCurrent({ value: true })));
    primary.appendChild(answerBtn(t('grill_no'), () => answerCurrent({ value: false })));
  } else if (q.answer_kind === 'choice') {
    (q.choices || []).forEach(c =>
      primary.appendChild(answerBtn(t('marital_' + c), () => answerCurrent({ value: c }))));
  } else {
    // money / integer — the field itself IS the free-form answer (no separate "Other").
    const isMoney = q.answer_kind === 'money';
    const field = el('div', { class: 'grill-num-field' + (isMoney ? ' is-money' : '') });
    if (isMoney) field.appendChild(el('span', { class: 'grill-num-prefix', 'aria-hidden': 'true', text: 'RM' }));
    const input = el('input', {
      type: 'number', min: '0', step: '1', class: 'grill-num-input',
      inputmode: 'numeric', 'aria-label': t('grill_answer_aria'),
      placeholder: isMoney ? t('grill_amount_placeholder') : ''
    });
    const submit = el('button', { type: 'button', class: 'grill-num-send', text: t('grill_submit') });
    const go = () => {
      const raw = input.value.trim();
      const num = Number(raw);
      if (raw === '' || Number.isNaN(num) || num < 0) {
        showInlineError(t('grill_amount_invalid'));
        input.focus();
        return;
      }
      clearInlineError();
      answerCurrent({ value: num });
    };
    submit.addEventListener('click', go);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
    field.appendChild(input);
    field.appendChild(submit);
    primary.appendChild(field);
  }

  if (q.skippable) {
    primary.appendChild(answerBtn(t('grill_skip'), () => answerCurrent({ skip: true }),
      'grill-skip-btn'));
  }
  wrap.appendChild(primary);

  // "Other" — answer in your own words when the preset chips don't fit. Also the
  // correction path: a typed hard fact ("actually I'm married") overrides a server-side
  // presumption (facts beat presumptions in the trust core). Only shown for chip
  // questions; a number field already accepts free input, so a second textbox there
  // would be redundant clutter.
  if (isChip) wrap.appendChild(buildOtherControl());
  return wrap;
}

function buildOtherControl() {
  const other = el('div', { class: 'grill-other' });
  // A quiet "or" divider sets the free-text escape hatch apart from the primary chips,
  // keeping the chips the clear primary action.
  const divider = el('div', { class: 'grill-or', aria: { hidden: 'true' } });
  divider.appendChild(el('span', { class: 'grill-or-text', text: t('chat_or') }));
  other.appendChild(divider);
  other.appendChild(el('label', { class: 'grill-other-label', for: 'grill-other-input',
    text: t('chat_other_label') }));
  const row = el('div', { class: 'grill-other-row' });
  const input = el('input', { type: 'text', id: 'grill-other-input', class: 'grill-other-input',
    maxlength: '400', 'aria-label': t('chat_other_label'), placeholder: t('chat_other_ph') });
  const send = el('button', { type: 'button', class: 'grill-other-send', text: t('chat_send') });
  const go = () => {
    const text = input.value.trim();
    if (!text) { input.focus(); return; }
    clearInlineError();
    answerCurrent({ other: text });
  };
  send.addEventListener('click', go);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  row.appendChild(input);
  row.appendChild(send);
  other.appendChild(row);
  return other;
}

function renderGrillCurrent() {
  clearEl(grillCurrentEl);
  if (!grillCurrent) return;
  const q = grillCurrent;
  // The Interview agent's warm phrasing (turn.reply) only while the UI language matches
  // the language it was generated in; otherwise (toggle) the static template renders.
  const qText = (q._reply && q._lang === currentLang)
    ? q._reply : t('q_' + q.field);
  const card = el('div', { class: 'grill-q-card' });
  card.appendChild(el('div', { class: 'grill-q-text', text: qText }));
  // No separate "why" chip: the Interview agent's phrased question (turn.reply, shown
  // above and localized to the display language) already names the benefit it unlocks.
  // The old chip drew from programs[].name_ms — Malay-only — so it leaked Malay in
  // English/中文/தமிழ் mode; the localized reply replaces it.
  card.appendChild(buildAnswerControls(q));
  grillCurrentEl.appendChild(card);
  announce(qText);
}

function renderResults(result, translationOk) {
  setIntakePhase('results');            // the journey rail reaches its final phase
  renderTranslationNotice(translationOk);
  renderGroundedness(result.groundedness);

  if (result.refused || result.ok === false) {
    renderRefusal(result.message_ms || t('refusal_fallback'));
    renderPipeline(result.stages);
    resultsSection.classList.add('visible');
    resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    announce(t('refusal_announce'));
    return;
  }

  renderPipeline(result.stages);
  renderTotalBanner(result.total_monthly_min);
  renderMessage(result.message_ms);
  renderAssumptions(result.assumptions_ms);
  renderEligible(result.eligible);
  renderGaps(result.gaps);
  renderNextSteps(result);
  renderCitations(result.citations);

  resultsSection.classList.add('visible');
  resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

  const eligibleCount = (result.eligible || []).length;
  const gapsCount = (result.gaps || []).length;
  announce(
    t('results_done') +
    (eligibleCount > 0 ? t('results_eligible_found', { n: eligibleCount }) : t('results_none_found')) +
    (gapsCount > 0 ? t('results_gaps_found', { n: gapsCount }) : '')
  );
}

// ===== Init =================================================================
applyChrome(currentLang);
setIntakePhase('describe');     // step 1 active on first paint
refreshThemeLabel();            // label matches the theme the head script applied
