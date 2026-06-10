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

let currentLang        = langSelect ? langSelect.value : 'en';
let lastAssessmentText = '';
let canonicalMsResult  = null;          // Malay canonical from the last assessment
let resultCacheByLang  = {};            // lang -> localized result dict

// Grill (adaptive interview) state. The client holds the whole session; the server
// is stateless. Questions/answers are stored language-neutrally (by field) so a
// language toggle re-renders them instantly with no network call.
let grillActive       = false;
let grillFacts        = {};             // established facts so far (Applicant subset)
let grillPresumed     = {};             // LLM-presumed soft facts {field: {value, reason_ms}}
let grillAsked        = [];             // fields already asked (incl. skipped)
let grillQuery        = '';             // Malay retrieval query from /grill/start
let grillCurrent      = null;           // the active question object, or null
let grillTranscript   = [];             // [{field, kind, value, skipped}]
let grillLastProgress = null;           // last progress dict (for re-render on toggle)
let grillBusy         = false;          // one in-flight grill call at a time — an answer
                                        // and a chip-dismissal must never race

// Store the active question stamped with the language its phrased text was
// generated in — on a language toggle the static template takes over instead.
function setGrillCurrent(question) {
  grillCurrent = question ? { ...question, _lang: currentLang } : null;
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
    renderResults(resultCacheByLang[lang], true);
    return;
  }
  if (lang === 'ms') {
    resultCacheByLang.ms = canonicalMsResult;
    renderResults(canonicalMsResult, true);
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
    renderResults(data.result, data.translation_ok);
  } catch (_err) {
    // Fall back to the verified Malay canonical, visibly.
    renderResults(canonicalMsResult, false);
  }
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
  // Break the explanation at its numbered markers ("(1) … (2) … (3) …") so each
  // section is scannable, and lift the leading "(n) …:" label into a heading.
  // Falls back to a single block when the text has no such structure.
  const blocks = messageText.split(/\n(?=\s*\(\d+\))/).map(b => b.trim()).filter(Boolean);
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

// Open the interview from the free-text paragraph, then let the engine drive.
btnAssess.addEventListener('click', async () => {
  const text = situationInput.value.trim();
  if (!text) {
    showInlineError(t('err_empty'));
    situationInput.focus();
    return;
  }
  clearInlineError();
  setLoading(true);
  clearResultsUI();
  setIntakePhase('describe');          // reset the journey rail for a fresh run
  lastAssessmentText = text;           // the opening paragraph, reused for appeals
  grillActive = true;
  grillFacts = {}; grillPresumed = {}; grillAsked = []; grillQuery = '';
  grillCurrent = null; grillTranscript = []; grillLastProgress = null;
  [grillProgressEl, grillPresumedEl, grillTranscriptEl, grillCurrentEl].forEach(clearEl);

  try {
    const resp = await fetch('/grill/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, lang: currentLang })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: t('err_server') }));
      throw new Error(err.detail || 'HTTP ' + resp.status);
    }
    const data = await resp.json();
    if (data.blocked) {
      grillActive = false;
      grillPanel.classList.add('hidden');
      showInlineError(t('grill_blocked'));
      return;
    }
    grillFacts = data.facts || {};
    grillPresumed = data.presumed || {};
    grillAsked = data.asked || [];
    grillQuery = data.retrieval_query_ms || '';
    grillLastProgress = data.progress || null;
    grillPanel.classList.remove('hidden');
    setIntakePhase('questions');         // advance the rail to the interview phase
    if (data.done) { await finishGrill(); return; }
    setGrillCurrent(data.question);
    renderGrill(data.progress);
  } catch (err) {
    grillActive = false;
    showInlineError(t('err_connection') + ' (' + (err.message || '') + ')');
    announce(t('err_connection_announce'));
  } finally {
    setLoading(false);
  }
});

// Send one structured answer (or skip) and advance to the next gap.
async function grillNext(payload) {
  if (grillBusy) return;
  grillBusy = true;
  const answered = grillCurrent;                 // the question being answered now
  try {
    const resp = await fetch('/grill/next', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        facts: grillFacts, presumed: grillPresumed, asked: grillAsked,
        field: payload.field,
        value: payload.skip ? null : payload.value, skip: !!payload.skip,
        text: lastAssessmentText, lang: currentLang
      })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: t('err_server') }));
      throw new Error(err.detail || 'HTTP ' + resp.status);
    }
    const data = await resp.json();
    grillTranscript = grillTranscript.concat([{
      field: payload.field, kind: answered ? answered.answer_kind : 'boolean',
      value: payload.skip ? null : payload.value, skipped: !!payload.skip
    }]);
    grillFacts = data.facts;
    grillPresumed = data.presumed || {};
    grillAsked = data.asked;
    grillLastProgress = data.progress;
    if (data.done) {
      setGrillCurrent(null);
      renderGrill(data.progress);
      await finishGrill();
      return;
    }
    setGrillCurrent(data.question);
    renderGrill(data.progress);
  } catch (err) {
    showInlineError(t('err_connection') + ' (' + (err.message || '') + ')');
  } finally {
    grillBusy = false;
  }
}

// Run the gathered profile through the same verified pipeline as before.
async function finishGrill() {
  clearEl(grillCurrentEl);
  grillCurrentEl.appendChild(el('div', { class: 'grill-done', text: t('grill_done') }));
  announce(t('grill_done'));
  setLoading(true);
  try {
    const resp = await fetch('/grill/assess', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        facts: grillFacts, presumed: grillPresumed, retrieval_query_ms: grillQuery,
        lang: currentLang
      })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: t('err_server') }));
      throw new Error(err.detail || 'HTTP ' + resp.status);
    }
    const data = await resp.json();   // { lang, translation_ok, result, canonical_ms }
    canonicalMsResult = data.canonical_ms;
    resultCacheByLang = { ms: data.canonical_ms };
    resultCacheByLang[data.lang] = data.result;
    grillActive = false;
    renderResults(data.result, data.translation_ok);
  } catch (err) {
    showInlineError(t('err_connection') + ' (' + (err.message || '') + ')');
    announce(t('err_connection_announce'));
  } finally {
    setLoading(false);
  }
}

// ----- Grill rendering (all from i18n + stored state — language-toggle safe) -----

function renderGrill(progress) {
  renderGrillProgress(progress);
  renderGrillPresumed();
  renderGrillTranscript();
  renderGrillCurrent();
}

// ----- Presumption chips: LLM-proposed soft facts the user can veto --------------

function presumedValueLabel(field, value) {
  if (typeof value === 'boolean') return value ? t('grill_yes') : t('grill_no');
  if (field === 'marital_status') return t('marital_' + value);
  return String(value);
}

function renderGrillPresumed() {
  clearEl(grillPresumedEl);
  const fields = Object.keys(grillPresumed);
  if (!fields.length) return;
  grillPresumedEl.appendChild(el('div', { class: 'grill-presumed-title',
    text: t('grill_assumed_title') }));
  const wrap = el('div', { class: 'grill-presumed-chips' });
  fields.forEach(field => {
    const chip = el('span', { class: 'grill-presumed-chip', role: 'listitem' });
    chip.appendChild(el('span', {
      text: t('q_' + field) + ' — ' + presumedValueLabel(field, grillPresumed[field].value) }));
    const x = el('button', { type: 'button', class: 'grill-presumed-x',
      'aria-label': t('grill_assumed_remove'), text: '✕' });
    x.addEventListener('click', () => dismissPresumed(field));
    chip.appendChild(x);
    wrap.appendChild(chip);
  });
  grillPresumedEl.appendChild(wrap);
}

// Veto one presumption: the field becomes UNKNOWN again, so the engine puts it
// back in the question queue (a recompute call — no answer is applied). Local
// state is only committed from the server's response — on failure the chip stays.
async function dismissPresumed(field) {
  if (grillBusy) return;
  grillBusy = true;
  const nextPresumed = { ...grillPresumed };
  delete nextPresumed[field];
  try {
    const resp = await fetch('/grill/next', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ facts: grillFacts, presumed: nextPresumed, asked: grillAsked,
        text: lastAssessmentText, lang: currentLang })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: t('err_server') }));
      throw new Error(err.detail || 'HTTP ' + resp.status);
    }
    const data = await resp.json();
    grillFacts = data.facts;
    grillPresumed = data.presumed || {};
    grillAsked = data.asked;
    grillLastProgress = data.progress;
    setGrillCurrent(data.done ? null : data.question);
    renderGrill(data.progress);
    if (data.done) { await finishGrill(); }
  } catch (err) {
    showInlineError(t('err_connection') + ' (' + (err.message || '') + ')');
  } finally {
    grillBusy = false;
  }
}

function renderGrillProgress(progress) {
  clearEl(grillProgressEl);
  if (!progress) return;
  const pct = progress.total ? Math.round((100 * progress.decided) / progress.total) : 0;
  const bar = el('div', { class: 'grill-progress-bar', aria: { hidden: 'true' } });
  const fill = el('div', { class: 'grill-progress-fill' });
  fill.style.width = pct + '%';
  bar.appendChild(fill);
  grillProgressEl.appendChild(bar);
  grillProgressEl.appendChild(el('div', { class: 'grill-progress-text',
    text: t('grill_progress', {
      total: progress.total, decided: progress.decided, undecided: progress.undecided
    }) }));
}

function transcriptValueLabel(item) {
  if (item.skipped) return t('grill_skip');
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
  if (q.answer_kind === 'boolean') {
    wrap.appendChild(answerBtn(t('grill_yes'), () => grillNext({ field: q.field, value: true })));
    wrap.appendChild(answerBtn(t('grill_no'), () => grillNext({ field: q.field, value: false })));
  } else if (q.answer_kind === 'choice') {
    (q.choices || []).forEach(c =>
      wrap.appendChild(answerBtn(t('marital_' + c), () => grillNext({ field: q.field, value: c }))));
  } else {
    const isMoney = q.answer_kind === 'money';
    const input = el('input', {
      type: 'number', min: '0', step: isMoney ? '1' : '1', class: 'grill-input',
      inputmode: 'numeric', 'aria-label': t('grill_answer_aria'),
      placeholder: isMoney ? t('grill_amount_placeholder') : ''
    });
    const submit = answerBtn(t('grill_submit'), () => {
      const raw = input.value.trim();
      const num = Number(raw);
      if (raw === '' || Number.isNaN(num) || num < 0) {
        showInlineError(t('grill_amount_invalid'));
        input.focus();
        return;
      }
      clearInlineError();
      grillNext({ field: q.field, value: num });
    });
    input.addEventListener('keydown', e => { if (e.key === 'Enter') submit.click(); });
    if (isMoney) {
      const prefix = el('span', { class: 'grill-input-prefix', 'aria-hidden': 'true', text: 'RM' });
      wrap.appendChild(prefix);
    }
    wrap.appendChild(input);
    wrap.appendChild(submit);
  }
  if (q.skippable) {
    wrap.appendChild(answerBtn(t('grill_skip'), () => grillNext({ field: q.field, skip: true }),
      'grill-skip-btn'));
  }
  return wrap;
}

function renderGrillCurrent() {
  clearEl(grillCurrentEl);
  if (!grillCurrent) return;
  const q = grillCurrent;
  // Contextual phrasing only while the UI language matches the language it was
  // generated in; otherwise (toggle, phrasing failure) the static template renders.
  const qText = (q.question_text && q._lang === currentLang)
    ? q.question_text : t('q_' + q.field);
  const card = el('div', { class: 'grill-q-card' });
  card.appendChild(el('div', { class: 'grill-q-text', text: qText }));

  if (q.programs && q.programs.length) {
    const chip = el('div', { class: 'grill-why' });
    chip.appendChild(el('span', { class: 'grill-why-icon', 'aria-hidden': 'true', text: '💡 ' }));
    const names = q.programs.slice(0, 2).map(p => {
      const amt = formatAmount(p.amount);
      return p.name_ms + (amt ? ' (' + amt + ')' : '');
    }).join('; ');
    chip.appendChild(el('span', { text: t('grill_why_prefix') + names }));
    card.appendChild(chip);
  }

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
