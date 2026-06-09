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
const citationsContainer    = document.getElementById('citations-container');

const grillPanel       = document.getElementById('grill');
const grillProgressEl  = document.getElementById('grill-progress');
const grillTranscriptEl = document.getElementById('grill-transcript');
const grillCurrentEl   = document.getElementById('grill-current');

let currentLang        = langSelect ? langSelect.value : 'en';
let lastAssessmentText = '';
let canonicalMsResult  = null;          // Malay canonical from the last assessment
let resultCacheByLang  = {};            // lang -> localized result dict

// Grill (adaptive interview) state. The client holds the whole session; the server
// is stateless. Questions/answers are stored language-neutrally (by field) so a
// language toggle re-renders them instantly with no network call.
let grillActive       = false;
let grillFacts        = {};             // established facts so far (Applicant subset)
let grillAsked        = [];             // fields already asked (incl. skipped)
let grillQuery        = '';             // Malay retrieval query from /grill/start
let grillAssumptions  = [];             // intake assumptions carried to /grill/assess
let grillCurrent      = null;           // the active question object, or null
let grillTranscript   = [];             // [{field, kind, value, skipped}]
let grillLastProgress = null;           // last progress dict (for re-render on toggle)

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

function announce(msg) {
  srStatus.textContent = '';
  setTimeout(() => { srStatus.textContent = msg; }, 50);
}

function formatAmount(amount) {
  if (!amount) return '';
  if (amount.type === 'fixed') {
    return 'RM' + amount.monthly_myr + t('per_month');
  }
  if (amount.type === 'range') {
    let str = 'RM' + amount.monthly_myr_min + '–RM' + amount.monthly_myr_max + t('per_month');
    if (amount.note_ms) str += ' (' + amount.note_ms + ')';  // note_ms is localized server-side
    return str;
  }
  return '';
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

function renderPipeline(stages) {
  clearEl(pipelineContainer);
  if (!stages || stages.length === 0) return;
  pipelineContainer.appendChild(el('h2', { text: t('pipeline_heading') }));
  const stageList = el('div', { class: 'pipeline-stages', role: 'list' });
  stages.forEach(stage => {
    const status = stage.status || 'ok';
    const isOk = status === 'ok';
    const icon = isOk ? '✓' : '✗';
    const chip = el('div', {
      class: 'stage-chip ' + status,
      role: 'listitem',
      title: stage.summary || stage.name,
      aria: { label: stage.name + ': ' + status + (stage.summary ? '. ' + stage.summary : '') }
    });
    chip.appendChild(el('span', { class: 'stage-icon', 'aria-hidden': 'true', text: icon }));
    chip.appendChild(el('span', { class: 'stage-name', text: stage.name }));
    if (stage.summary) {
      chip.appendChild(el('span', { class: 'stage-summary', text: ' — ' + stage.summary }));
    }
    stageList.appendChild(chip);
  });
  pipelineContainer.appendChild(stageList);
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

function renderMessage(messageText) {
  clearEl(messageContainer);
  if (!messageText) return;
  const card = el('div', { class: 'message-card' });
  const p = el('p');
  p.textContent = messageText;  // textContent only — white-space:pre-wrap in CSS
  card.appendChild(p);
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
  const nameWrap = el('div');
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

function renderEligible(eligible) {
  clearEl(eligibleContainer);
  if (!eligible || eligible.length === 0) return;
  const section = el('div', { class: 'results-section' });
  section.appendChild(el('h2', { text: t('eligible_heading') + ' (' + eligible.length + ')' }));
  const list = el('div', { class: 'benefit-list' });
  eligible.forEach(program => {
    const card = el('div', { class: 'benefit-card' });
    card.appendChild(buildProgramHeader('benefit-card-header', program));
    appendCitation(card, program.citation);
    list.appendChild(card);
  });
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
    if (gap.near_miss) card.appendChild(el('span', { class: 'near-miss-badge', text: t('near_miss_badge') }));
    card.appendChild(buildProgramHeader('gap-card-header', gap));

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
   citationsContainer].forEach(clearEl);
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
  lastAssessmentText = text;           // the opening paragraph, reused for appeals
  grillActive = true;
  grillFacts = {}; grillAsked = []; grillQuery = ''; grillAssumptions = [];
  grillCurrent = null; grillTranscript = []; grillLastProgress = null;
  [grillProgressEl, grillTranscriptEl, grillCurrentEl].forEach(clearEl);

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
    grillAsked = data.asked || [];
    grillQuery = data.retrieval_query_ms || '';
    grillAssumptions = data.assumptions_ms || [];
    grillLastProgress = data.progress || null;
    grillPanel.classList.remove('hidden');
    if (data.done) { await finishGrill(); return; }
    grillCurrent = data.question;
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
  const answered = grillCurrent;                 // the question being answered now
  try {
    const resp = await fetch('/grill/next', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        facts: grillFacts, asked: grillAsked, field: payload.field,
        value: payload.skip ? null : payload.value, skip: !!payload.skip
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
    grillAsked = data.asked;
    grillLastProgress = data.progress;
    if (data.done) {
      grillCurrent = null;
      renderGrill(data.progress);
      await finishGrill();
      return;
    }
    grillCurrent = data.question;
    renderGrill(data.progress);
  } catch (err) {
    showInlineError(t('err_connection') + ' (' + (err.message || '') + ')');
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
        facts: grillFacts, retrieval_query_ms: grillQuery,
        assumptions_ms: grillAssumptions, lang: currentLang
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
  renderGrillTranscript();
  renderGrillCurrent();
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
  const card = el('div', { class: 'grill-q-card' });
  card.appendChild(el('div', { class: 'grill-q-text', text: t('q_' + q.field) }));

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
  announce(t('q_' + q.field));
}

function renderResults(result, translationOk) {
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
