/* BenefitNavigator Malaysia — vanilla JS UI, no build step */
'use strict';

const langSelect      = document.getElementById('lang-select');
const btnTextSize     = document.getElementById('btn-text-size');
const situationInput  = document.getElementById('situation-input');
const btnAssess       = document.getElementById('btn-assess');
const srStatus        = document.getElementById('sr-status');
const inlineError     = document.getElementById('inline-error');
const inlineErrorText = document.getElementById('inline-error-text');
const resultsSection  = document.getElementById('results');

const groundednessContainer = document.getElementById('groundedness-container');
const refusalContainer      = document.getElementById('refusal-container');
const totalBannerContainer  = document.getElementById('total-banner-container');
const pipelineContainer     = document.getElementById('pipeline-container');
const messageContainer      = document.getElementById('message-container');
const assumptionsContainer  = document.getElementById('assumptions-container');
const eligibleContainer     = document.getElementById('eligible-container');
const gapsContainer         = document.getElementById('gaps-container');
const citationsContainer    = document.getElementById('citations-container');

let currentLang        = 'ms';
let lastAssessmentText = '';

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
    return 'RM' + amount.monthly_myr + '/bulan';
  }
  if (amount.type === 'range') {
    let str = 'RM' + amount.monthly_myr_min + '–RM' + amount.monthly_myr_max + '/bulan';
    if (amount.note_ms) str += ' (' + amount.note_ms + ')';
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
  const label = 'Sumber: ' + (citation.doc_title || '') +
    (citation.locator ? ', ' + citation.locator : '');
  const url = citation.source_url || '';
  const isHttpUrl = /^https?:\/\//i.test(url);
  if (isHttpUrl) {
    const a = el('a', { href: url, target: '_blank', rel: 'noopener noreferrer', text: label });
    return a;
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

langSelect.addEventListener('change', () => {
  currentLang = langSelect.value;
  document.documentElement.lang = currentLang;
});

btnTextSize.addEventListener('click', () => {
  const isLarge = document.documentElement.classList.toggle('text-large');
  btnTextSize.setAttribute('aria-pressed', String(isLarge));
  announce(isLarge ? 'Teks besar diaktifkan' : 'Saiz teks normal');
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
    btnAssess.appendChild(document.createTextNode(' Sedang menyemak…'));
    announce('Sedang menyemak kelayakan anda. Sila tunggu sebentar.');
  } else {
    btnAssess.appendChild(document.createTextNode('Semak Kelayakan'));
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

function renderPipeline(stages) {
  clearEl(pipelineContainer);
  if (!stages || stages.length === 0) return;
  pipelineContainer.appendChild(el('h2', { text: 'Bagaimana kami menyemak' }));
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

function renderRefusal(messageMsText) {
  clearEl(refusalContainer);
  const banner = el('div', { class: 'refusal-banner', role: 'alert' });
  banner.appendChild(el('h2', { text: 'Permohonan Tidak Dapat Diproses' }));
  const msg = el('p', { text: messageMsText });
  msg.style.whiteSpace = 'pre-wrap';
  banner.appendChild(msg);
  const hotline = el('p', { class: 'hotline' });
  hotline.textContent = 'Untuk bantuan lanjut, hubungi: Talian Kasih ';
  const num = el('a', { href: 'tel:15999', text: '15999' });
  hotline.appendChild(num);
  banner.appendChild(hotline);
  refusalContainer.appendChild(banner);
}

function renderGroundedness(g) {
  clearEl(groundednessContainer);
  if (!g || !g.available) return;
  const isOk = g.grounded;
  const badge = el('div', { class: 'groundedness-badge ' + (isOk ? 'verified' : 'unverified'), role: 'status' });
  badge.appendChild(el('span', { 'aria-hidden': 'true', text: isOk ? '✓ ' : '⚠ ' }));
  badge.appendChild(el('span', { text: isOk ? 'Disahkan dengan sumber rasmi' : 'Sebahagian maklumat belum disahkan sepenuhnya' }));
  if (typeof g.ungrounded_percentage === 'number') {
    badge.appendChild(el('span', { class: 'badge-sub', text: ' (' + g.ungrounded_percentage.toFixed(0) + '% tidak disahkan)' }));
  }
  groundednessContainer.appendChild(badge);
}

function renderTotalBanner(totalMin) {
  clearEl(totalBannerContainer);
  if (!totalMin || totalMin <= 0) return;

  const banner = el('div', { class: 'total-banner', role: 'status' });
  banner.appendChild(el('div', { class: 'total-amount', text: 'Anda mungkin layak ~RM' + totalMin + '/bulan' }));
  banner.appendChild(el('div', { class: 'total-label', text: 'Anggaran minimum bantuan yang layak anda terima' }));
  totalBannerContainer.appendChild(banner);
}

function renderMessage(messageMsText) {
  clearEl(messageContainer);
  if (!messageMsText) return;
  const card = el('div', { class: 'message-card' });
  const p = el('p');
  p.textContent = messageMsText;  // textContent only — white-space:pre-wrap in CSS
  card.appendChild(p);
  messageContainer.appendChild(card);
}

function renderAssumptions(assumptions) {
  clearEl(assumptionsContainer);
  if (!assumptions || assumptions.length === 0) return;

  const card = el('div', { class: 'assumptions-card' });
  card.appendChild(el('h3', { text: 'Andaian kami' }));
  const ul = el('ul');
  assumptions.forEach(a => {
    ul.appendChild(el('li', { text: a }));
  });
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
  section.appendChild(el('h2', { text: 'Bantuan Yang Anda Layak (' + eligible.length + ')' }));
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
  section.appendChild(el('h2', { text: 'Bantuan Yang Hampir Layak / Tidak Layak (' + gaps.length + ')' }));
  gaps.forEach(gap => {
    const card = el('div', { class: 'gap-card' + (gap.near_miss ? ' near-miss' : '') });
    if (gap.near_miss) card.appendChild(el('span', { class: 'near-miss-badge', text: 'Hampir Layak' }));
    card.appendChild(buildProgramHeader('gap-card-header', gap));

    appendListSection(card, 'gap-blocking', 'Sebab tidak layak:', gap.blocking_ms);
    appendListSection(card, 'gap-actions', 'Langkah untuk layak:', gap.actions_ms);
    appendCitation(card, gap.citation);

    const appealRegionId = 'appeal-' + (gap.program_id || Math.random().toString(36).slice(2));
    const btnAppeal = el('button', {
      type: 'button', class: 'btn-appeal', text: 'Draf Surat Rayuan',
      'aria-expanded': 'false', 'aria-controls': appealRegionId
    });
    card.appendChild(btnAppeal);

    const appealRegion = el('div', { id: appealRegionId, class: 'appeal-region hidden' });
    card.appendChild(appealRegion);

    btnAppeal.addEventListener('click', () => {
      handleAppeal(gap, btnAppeal, appealRegion);
    });

    section.appendChild(card);
  });

  gapsContainer.appendChild(section);
}

function renderCitations(citations) {
  clearEl(citationsContainer);
  if (!citations || citations.length === 0) return;

  const section = el('div', { class: 'results-section' });
  section.appendChild(el('h2', { text: 'Rujukan Sumber' }));
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
  appealRegion.appendChild(el('div', { class: 'appeal-loading', text: 'Sedang merangka surat rayuan…' }));
  btnAppeal.disabled = true;
  try {
    const resp = await fetch('/appeal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: lastAssessmentText, program_id: gap.program_id, lang: currentLang })
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: 'Ralat tidak diketahui' }));
      throw new Error(err.detail || 'HTTP ' + resp.status);
    }

    const letter = await resp.json();
    renderAppealLetter(appealRegion, letter);
    appealRegion.dataset.loaded = 'true';
  } catch (err) {
    clearEl(appealRegion);
    appealRegion.appendChild(
      el('div', { class: 'appeal-loading', text: 'Gagal merangka surat: ' + (err.message || 'Ralat rangkaian') })
    );
  } finally {
    btnAppeal.disabled = false;
  }
}

function renderAppealLetter(container, letter) {
  clearEl(container);

  const header = el('div', { class: 'appeal-region-header' });
  header.appendChild(el('h4', { text: 'Draf Surat Rayuan — ' + (letter.program_name_ms || letter.program_id || '') }));

  const btnCopy = el('button', { type: 'button', class: 'btn-copy', text: 'Salin' });
  header.appendChild(btnCopy);
  container.appendChild(header);

  const body = el('div', { class: 'appeal-body' });

  const letterPre = el('pre', { class: 'appeal-letter' });
  letterPre.textContent = letter.body_ms || '';
  body.appendChild(letterPre);

  if (letter.routing_ms) {
    const routing = el('div', { class: 'appeal-routing' });
    routing.appendChild(el('strong', { text: 'Cara penghantaran: ' }));
    routing.appendChild(document.createTextNode(letter.routing_ms));
    body.appendChild(routing);
  }

  container.appendChild(body);

  btnCopy.addEventListener('click', () => {
    const text = (letter.body_ms || '') +
      (letter.routing_ms ? '\n\n--- Cara penghantaran ---\n' + letter.routing_ms : '');
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(() => {
        btnCopy.textContent = 'Disalin!';
        btnCopy.classList.add('copied');
        setTimeout(() => {
          btnCopy.textContent = 'Salin';
          btnCopy.classList.remove('copied');
        }, 2000);
      }).catch(() => fallbackCopy(text, btnCopy));
    } else {
      fallbackCopy(text, btnCopy);
    }
  });
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
    btn.textContent = 'Disalin!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'Salin'; btn.classList.remove('copied'); }, 2000);
  } catch (_) {
    btn.textContent = 'Salin';
  }
  document.body.removeChild(ta);
}

btnAssess.addEventListener('click', async () => {
  const text = situationInput.value.trim();
  if (!text) {
    showInlineError('Sila ceritakan keadaan anda terlebih dahulu.');
    situationInput.focus();
    return;
  }

  clearInlineError();
  setLoading(true);
  lastAssessmentText = text;
  resultsSection.classList.remove('visible');
  [groundednessContainer, refusalContainer, totalBannerContainer, pipelineContainer,
   messageContainer, assumptionsContainer, eligibleContainer, gapsContainer,
   citationsContainer].forEach(clearEl);

  try {
    const resp = await fetch('/assess', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, lang: currentLang })
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: 'Ralat pelayan' }));
      throw new Error(err.detail || 'HTTP ' + resp.status);
    }

    const data = await resp.json();
    renderResults(data);

  } catch (err) {
    showInlineError(
      'Ralat sambungan. Sila cuba semula. ' +
      'Jika masalah berterusan, hubungi Talian Kasih 15999. (' + (err.message || '') + ')'
    );
    announce('Ralat semakan. Sila cuba semula atau hubungi Talian Kasih 15999.');
  } finally {
    setLoading(false);
  }
});

function renderResults(data) {
  renderGroundedness(data.groundedness);
  if (data.refused || data.ok === false) {
    renderRefusal(data.message_ms || 'Permintaan tidak dapat diproses.');
    renderPipeline(data.stages);
    resultsSection.classList.add('visible');
    resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    announce('Permintaan tidak dapat diproses. Sila semak mesej di skrin.');
    return;
  }

  renderPipeline(data.stages);
  renderTotalBanner(data.total_monthly_min);
  renderMessage(data.message_ms);
  renderAssumptions(data.assumptions_ms);
  renderEligible(data.eligible);
  renderGaps(data.gaps);
  renderCitations(data.citations);

  resultsSection.classList.add('visible');
  resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

  const eligibleCount = (data.eligible || []).length;
  const gapsCount = (data.gaps || []).length;
  announce(
    'Semakan selesai. ' +
    (eligibleCount > 0 ? eligibleCount + ' bantuan yang layak ditemui. ' : 'Tiada bantuan layak ditemui. ') +
    (gapsCount > 0 ? gapsCount + ' bantuan hampir layak. ' : '')
  );
}
