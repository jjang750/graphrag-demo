let allMenuItems = [];   // 전체 MenuItem
let selectedMenuItemId = null;
let selectedQaId = null;
let qaTab = 'linked';    // 'linked' | 'unlinked' | 'search' | 'review'
let reviewData = [];     // 검토 탭 데이터 캐시

// 메뉴관계 탭 상태
let relSrcId = null, relSrcPath = '';
let relTgtId = null, relTgtPath = '';
let relMenuItems = [];  // 관계 탭용 메뉴 목록 캐시
let relCountBySource = {};  // 소스 메뉴별 연결된 관계 수

// ─────────────────────────────────────────
// 메인 탭 전환
// ─────────────────────────────────────────
function showMainTab(tab) {
    ['qa', 'relation', 'desc'].forEach(t => {
        const content = document.getElementById(`tab-${t}`);
        const btn = document.getElementById(`main-tab-${t}`);
        if (content) content.style.display = t === tab ? 'flex' : 'none';
        if (btn) btn.classList.toggle('active', t === tab);
    });
    if (tab === 'relation') {
        relMenuItems = allMenuItems.length ? [...allMenuItems] : [];
        if (relMenuItems.length) {
            filterRelList('src');
            filterRelList('tgt');
        } else {
            fetch('/admin/menus').then(r => r.json()).then(data => {
                relMenuItems = data.items || [];
                filterRelList('src');
                filterRelList('tgt');
            });
        }
        loadRelations();
    }
    if (tab === 'desc') {
        loadDescMenuList();
    }
}

// ─────────────────────────────────────────
// 초기화
// ─────────────────────────────────────────
async function init() {
    await Promise.all([loadMenuTree(), loadSources(), loadStats()]);
    populateReviewSourceSelect();
    showMainTab('desc');   // 기본 탭: 메뉴설명
}

async function loadStats() {
    try {
        const [menuRes, qaRes] = await Promise.all([
            fetch('/admin/menus').then(r => r.json()),
            fetch('/admin/qa?limit=1').then(r => r.json())
        ]);
        const total = menuRes.items?.length || 0;
        const linked = menuRes.items?.filter(m => m.qa_count > 0).length || 0;
        document.getElementById('statMenus').textContent = `메뉴: ${total}개`;
        document.getElementById('statLinked').textContent = `QA연결: ${linked}개 메뉴`;
    } catch(e) {}
}

async function loadSources() {
    try {
        const res = await fetch('/admin/sources');
        const data = await res.json();
        const sel = document.getElementById('qaSource');
        data.items.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.source;
            opt.textContent = `${s.source} (${s.count})`;
            sel.appendChild(opt);
        });
    } catch(e) {}
}

// ─────────────────────────────────────────
// 메뉴 트리
// ─────────────────────────────────────────
async function loadMenuTree() {
    try {
        const res = await fetch('/admin/menus');
        const data = await res.json();
        allMenuItems = data.items || [];
        fillMainMenuOptions('globalMainFilter', allMenuItems, '(없음)');
        filterMenuTree();
    } catch(e) {
        document.getElementById('menuTree').innerHTML = '<div class="empty">로드 실패</div>';
    }
}

// 대분류(main_menu) 드롭다운 옵션 채우기 (모든 탭 공통)
function fillMainMenuOptions(selectId, items, fallback) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    const fb = fallback || '(없음)';
    const prev = sel.value;
    const mains = [...new Set(items.map(m => m.main_menu || fb))].sort();
    sel.innerHTML = '<option value="">전체 대분류</option>' +
        mains.map(name => `<option value="${escHtml(name)}">${escHtml(name)}</option>`).join('');
    if (prev && mains.includes(prev)) sel.value = prev;
}

// 상단 전역 대분류 필터 — 모든 탭 공통 적용
function currentMainFilter() {
    const el = document.getElementById('globalMainFilter');
    return el ? el.value : '';
}

function applyGlobalMainFilter() {
    if (document.getElementById('menuTree')) filterMenuTree();
    if (typeof relMenuItems !== 'undefined' && relMenuItems.length) {
        filterRelList('src');
        filterRelList('tgt');
    }
    if (typeof descAllItems !== 'undefined' && descAllItems.length) filterDescList();
}

function filterMenuTree() {
    const q = document.getElementById('menuSearch').value.trim().toLowerCase();
    const main = currentMainFilter();
    let filtered = allMenuItems;
    if (main) filtered = filtered.filter(m => (m.main_menu || '(없음)') === main);
    if (q) filtered = filtered.filter(m => m.name.toLowerCase().includes(q) ||
              (m.menu_path || '').toLowerCase().includes(q));
    renderMenuTree(filtered);
}

function renderMenuTree(items) {
    const container = document.getElementById('menuTree');
    if (!items.length) { container.innerHTML = '<div class="empty">검색 결과 없음</div>'; return; }

    // 계층 구조 빌드
    const tree = {};  // main_menu → sub_menu → [items]
    items.forEach(m => {
        const main = m.main_menu || '(없음)';
        const sub  = m.sub_menu  || '(직속)';
        if (!tree[main]) tree[main] = {};
        if (!tree[main][sub]) tree[main][sub] = [];
        tree[main][sub].push(m);
    });

    let html = '';
    for (const main of Object.keys(tree).sort()) {
        const mainId = `main-${main}`;
        const subGroups = tree[main];
        const totalQa = Object.values(subGroups).flat().reduce((s, m) => s + (m.qa_count || 0), 0);

        html += `<div class="menu-group">
            <div class="menu-group-header" onclick="toggleGroup('${mainId}')">
                📁 ${escHtml(main)}
                <span class="qa-badge ${totalQa > 0 ? 'has-qa' : 'no-qa'}">${totalQa}</span>
                <span class="collapse-icon" id="icon-${mainId}">▼</span>
            </div>
            <div id="${mainId}">`;

        for (const sub of Object.keys(subGroups).sort()) {
            const subItems = subGroups[sub];
            const subQa = subItems.reduce((s, m) => s + (m.qa_count || 0), 0);

            if (sub === '(직속)') {
                subItems.forEach(m => {
                    html += menuItemRow(m);
                });
            } else {
                const subId = `sub-${main}-${sub}`.replace(/\s/g, '_');
                html += `
                <div class="menu-sub-header" onclick="toggleGroup('${subId}')">
                    📂 ${escHtml(sub)}
                    <span class="qa-badge ${subQa > 0 ? 'has-qa' : 'no-qa'}">${subQa}</span>
                    <span class="collapse-icon" id="icon-${subId}">▼</span>
                </div>
                <div id="${subId}">`;
                subItems.forEach(m => { html += menuItemRow(m); });
                html += `</div>`;
            }
        }
        html += `</div></div>`;
    }
    container.innerHTML = html;
}

function menuItemRow(m) {
    const active = m.id === selectedMenuItemId ? ' active' : '';
    return `<div class="menu-item-row${active}" id="menu-row-${m.id}" onclick="selectMenuItem('${escHtml(m.id)}')">
        📄 ${escHtml(m.name)}
        <span class="qa-badge ${m.qa_count > 0 ? 'has-qa' : 'no-qa'}">${m.qa_count}</span>
    </div>`;
}

function toggleGroup(id) {
    const el = document.getElementById(id);
    const icon = document.getElementById('icon-' + id);
    if (!el) return;
    const hidden = el.style.display === 'none';
    el.style.display = hidden ? '' : 'none';
    if (icon) icon.textContent = hidden ? '▼' : '▶';
}

function selectMenuItem(id) {
    try {
        // 이전 active 해제
        if (selectedMenuItemId) {
            const prev = document.getElementById(`menu-row-${selectedMenuItemId}`);
            if (prev) prev.classList.remove('active');
        }
        selectedMenuItemId = id;
        const curr = document.getElementById(`menu-row-${id}`);
        if (curr) curr.classList.add('active');

        const item = allMenuItems.find(m => m.id === id);
        document.getElementById('qaListTitle').textContent = item ? item.name : id;
        loadQaList();
    } catch(e) {
        console.error('selectMenuItem error:', e);
    }
}

// ─────────────────────────────────────────
// QA 목록
// ─────────────────────────────────────────
function setQaTab(tab) {
    qaTab = tab;
    ['linked', 'unlinked', 'search', 'review'].forEach(t => {
        const el = document.getElementById(`tab-${t}`);
        if (el) el.classList.toggle('active', t === tab);
    });
    document.getElementById('qaFilterBar').style.display = tab === 'search' ? '' : 'none';
    document.getElementById('reviewBar').style.display  = tab === 'review' ? '' : 'none';
    if (tab === 'review') {
        renderReviewList();
    } else {
        loadQaList();
    }
}

async function loadQaList() {
    const listEl = document.getElementById('qaList');
    listEl.innerHTML = '<div class="loading-text">로딩 중...</div>';

    try {
        let url;
        if (qaTab === 'linked' && selectedMenuItemId) {
            url = `/admin/qa?menuitem_id=${encodeURIComponent(selectedMenuItemId)}&limit=100`;
        } else if (qaTab === 'unlinked') {
            const src = document.getElementById('qaSource').value;
            url = `/admin/qa?unlinked=true&limit=100${src ? '&source=' + encodeURIComponent(src) : ''}`;
        } else if (qaTab === 'search') {
            return; // 검색 버튼 클릭 시 실행
        } else {
            listEl.innerHTML = '<div class="empty">← 메뉴를 선택하세요</div>';
            return;
        }

        const res = await fetch(url);
        const data = await res.json();
        renderQaList(data.items || []);
    } catch(e) {
        listEl.innerHTML = '<div class="empty">로드 실패</div>';
    }
}

async function searchQa() {
    const q = document.getElementById('qaSearch').value.trim();
    const src = document.getElementById('qaSource').value;
    if (!q && !src) { alert('검색어 또는 소스를 입력하세요'); return; }

    const listEl = document.getElementById('qaList');
    listEl.innerHTML = '<div class="loading-text">검색 중...</div>';
    try {
        const params = new URLSearchParams({ limit: 50 });
        if (q)   params.set('q', q);
        if (src) params.set('source', src);
        const res = await fetch('/admin/qa?' + params.toString());
        const data = await res.json();
        renderQaList(data.items || []);
    } catch(e) {
        listEl.innerHTML = '<div class="empty">검색 실패</div>';
    }
}

function renderQaList(items) {
    const listEl = document.getElementById('qaList');
    if (!items.length) { listEl.innerHTML = '<div class="empty">QA가 없습니다</div>'; return; }

    listEl.innerHTML = items.map(qa => {
        const active = qa.id === selectedQaId ? ' active' : '';
        return `<div class="qa-card${active}" id="qa-card-${qa.id}" onclick="selectQa('${escHtml(qa.id)}')">
            <div class="qa-q">${escHtml(qa.question)}</div>
            <div class="qa-meta">
                <span class="qa-source">${escHtml(qa.source || '-')}</span>
                <span>${(qa.tags?.slice(0,3).map(t => '#'+escHtml(t)).join(' ')) || ''}</span>
            </div>
        </div>`;
    }).join('');
}

// ─────────────────────────────────────────
// QA 상세 + 연결 관리
// ─────────────────────────────────────────
async function selectQa(qaId) {
    try {
        if (selectedQaId) {
            const prev = document.getElementById(`qa-card-${selectedQaId}`);
            if (prev) prev.classList.remove('active');
        }
        selectedQaId = qaId;
        const curr = document.getElementById(`qa-card-${qaId}`);
        if (curr) curr.classList.add('active');

        await renderDetail(qaId);
    } catch(e) {
        console.error('selectQa error:', e);
    }
}

async function renderDetail(qaId) {
    const panel = document.getElementById('detailPanel');
    panel.innerHTML = '<div class="loading-text">로딩 중...</div>';

    try {
        // QA 상세 + 연결된 메뉴 목록 동시 조회
        const [qa, menuRes] = await Promise.all([
            fetch(`/admin/qa/${encodeURIComponent(qaId)}/detail`).then(r => {
                if (!r.ok) throw new Error('QA 조회 실패');
                return r.json();
            }),
            fetch(`/admin/qa/${encodeURIComponent(qaId)}/menus`).then(r => r.json())
        ]);

        const linkedMenus = menuRes.items || [];

        panel.innerHTML = `
        <!-- QA 내용 -->
        <div class="detail-section">
            <div class="detail-section-header">
                📌 ${escHtml(qa.id)}
                <span style="font-size:11px;color:#999">${qa.source || ''}</span>
                <button class="btn btn-ghost btn-sm" style="margin-left:auto"
                    onclick="toggleQaEdit('${escHtml(qaId)}')">✏️ 편집</button>
            </div>
            <!-- 보기 모드 -->
            <div class="detail-section-body" id="qaViewBody">
                <div class="detail-q">Q. ${escHtml(qa.question)}</div>
                <div class="detail-a">A. ${escHtml(qa.answer)}</div>
                <div class="tag-list">${(qa.tags||[]).map(t => `<span class="tag">#${escHtml(t)}</span>`).join('')}</div>
            </div>
            <!-- 편집 모드 (초기 숨김) -->
            <div class="detail-section-body" id="qaEditBody" style="display:none">
                <div class="qa-edit-label">질문</div>
                <textarea class="qa-edit-textarea" id="editQuestion" rows="3">${escHtml(qa.question)}</textarea>
                <div class="qa-edit-label" style="margin-top:10px">답변</div>
                <textarea class="qa-edit-textarea" id="editAnswer" rows="6">${escHtml(qa.answer)}</textarea>
                <div class="qa-edit-label" style="margin-top:10px">태그 <span style="font-weight:400;color:#999">(메뉴명 자동완성 / Enter로 추가)</span></div>
                <div class="tag-input-wrap" id="tagInputWrap" onclick="document.getElementById('tagInputField').focus()">
                    ${(qa.tags||[]).map(t => {
                        const isMenu = allMenuItems.some(m => m.name === t);
                        return `<span class="tag-chip${isMenu ? ' is-menu' : ''}" data-tag="${escHtml(t)}">${escHtml(t)}<span class="tag-chip-x" onclick="event.stopPropagation();removeTagChip(this)">×</span></span>`;
                    }).join('')}
                    <input class="tag-input-field" id="tagInputField" type="text"
                        placeholder="태그 입력..."
                        oninput="onTagInput()" onkeydown="onTagKeydown(event)"
                        onfocus="document.getElementById('tagInputWrap').classList.add('focus')"
                        onblur="setTimeout(()=>{document.getElementById('tagInputWrap').classList.remove('focus');hideTagAC()},200)">
                    <div class="tag-autocomplete" id="tagAutocomplete"></div>
                </div>
                <div class="qa-edit-actions" style="margin-top:12px">
                    <button class="btn btn-primary btn-sm" onclick="saveQaEdit('${escHtml(qaId)}')">💾 저장</button>
                    <button class="btn btn-ghost btn-sm" onclick="cancelQaEdit()">✕ 취소</button>
                    <span class="qa-edit-msg" id="qaEditMsg"></span>
                </div>
            </div>
        </div>

        <!-- 연결된 메뉴 -->
        <div class="detail-section">
            <div class="detail-section-header">
                🔗 연결된 메뉴 <span style="color:#2E7D32">(${linkedMenus.length}개)</span>
            </div>
            <div class="detail-section-body" id="linkedMenuList">
                ${linkedMenus.length === 0
                    ? '<div style="color:#999;font-size:12px">연결된 메뉴 없음</div>'
                    : linkedMenus.map(m => `
                        <div class="linked-menu-item">
                            <span class="linked-menu-path">📄 ${escHtml(m.menu_path || m.name)}</span>
                            <button class="btn btn-danger btn-sm"
                                onclick="unlinkMenu('${escHtml(qaId)}', '${escHtml(m.id)}', this)">연결 해제</button>
                        </div>`).join('')}
            </div>
        </div>

        <!-- 메뉴 연결 추가 -->
        <div class="detail-section">
            <div class="detail-section-header">➕ 메뉴 연결 추가</div>
            <div class="detail-section-body">
                <div class="filter-row" style="margin-bottom:8px">
                    <input type="text" id="menuLinkSearch" placeholder="메뉴 이름 검색..." onkeydown="if(event.key==='Enter') searchMenuForLink()">
                    <button class="btn btn-ghost btn-sm" onclick="searchMenuForLink()">검색</button>
                </div>
                ${selectedMenuItemId ? `
                <div style="margin-bottom:6px;font-size:12px;color:#555">
                    현재 선택된 메뉴:
                    <span style="font-weight:700;color:#1A237E">${escHtml(allMenuItems.find(m=>m.id===selectedMenuItemId)?.menu_path || selectedMenuItemId)}</span>
                    <button class="btn btn-success btn-sm" style="margin-left:6px"
                        onclick="linkCurrentMenu('${escHtml(qaId)}')">연결 추가</button>
                </div>` : ''}
                <div id="menuSearchResults"></div>
            </div>
        </div>`;
    } catch(e) {
        console.error('renderDetail error:', e);
        panel.innerHTML = `<div class="empty">로드 실패: ${escHtml(e.message)}</div>`;
    }
}

function toggleQaEdit(qaId) {
    const viewBody = document.getElementById('qaViewBody');
    const editBody = document.getElementById('qaEditBody');
    const isEditing = editBody.style.display !== 'none';
    if (isEditing) {
        cancelQaEdit();
    } else {
        viewBody.style.display = 'none';
        editBody.style.display = 'block';
        document.getElementById('editQuestion').focus();
    }
}

function cancelQaEdit() {
    document.getElementById('qaViewBody').style.display = 'block';
    document.getElementById('qaEditBody').style.display = 'none';
    document.getElementById('qaEditMsg').textContent = '';
    hideTagAC();
}

// ─────────────────────────────────────────
// 칩 태그 입력 + 메뉴 자동완성
// ─────────────────────────────────────────
let tagACIndex = -1;  // 자동완성 선택 인덱스

function getTagChips() {
    const wrap = document.getElementById('tagInputWrap');
    if (!wrap) return [];
    return Array.from(wrap.querySelectorAll('.tag-chip')).map(el => el.dataset.tag);
}

function addTagChip(text) {
    const tag = text.trim();
    if (!tag) return;
    // 중복 방지
    if (getTagChips().includes(tag)) return;

    const isMenu = allMenuItems.some(m => m.name === tag);
    const chip = document.createElement('span');
    chip.className = 'tag-chip' + (isMenu ? ' is-menu' : '');
    chip.dataset.tag = tag;
    chip.innerHTML = `${escHtml(tag)}<span class="tag-chip-x" onclick="event.stopPropagation();removeTagChip(this)">×</span>`;

    const input = document.getElementById('tagInputField');
    input.parentNode.insertBefore(chip, input);
    input.value = '';
    hideTagAC();
}

function removeTagChip(xBtn) {
    xBtn.parentElement.remove();
}

function onTagInput() {
    const input = document.getElementById('tagInputField');
    const q = input.value.trim().toLowerCase();
    tagACIndex = -1;

    if (q.length < 1) { hideTagAC(); return; }

    const existing = getTagChips();
    const matches = allMenuItems
        .filter(m => m.name.toLowerCase().includes(q) && !existing.includes(m.name))
        .slice(0, 8);

    const acEl = document.getElementById('tagAutocomplete');
    if (!matches.length) { hideTagAC(); return; }

    acEl.innerHTML = matches.map((m, i) =>
        `<div class="tag-ac-item" data-idx="${i}" onmousedown="addTagChip('${escHtml(m.name)}')">
            <span>${highlightMatch(escHtml(m.name), q)}</span>
            <span class="ac-path">${escHtml(m.menu_path || '')}</span>
        </div>`
    ).join('');
    acEl.style.display = 'block';
}

function highlightMatch(text, q) {
    const idx = text.toLowerCase().indexOf(q.toLowerCase());
    if (idx < 0) return text;
    return text.slice(0, idx) + '<b style="color:#1A237E">' + text.slice(idx, idx + q.length) + '</b>' + text.slice(idx + q.length);
}

function onTagKeydown(e) {
    const acEl = document.getElementById('tagAutocomplete');
    const items = acEl.querySelectorAll('.tag-ac-item');
    const visible = acEl.style.display === 'block' && items.length > 0;

    if (e.key === 'ArrowDown' && visible) {
        e.preventDefault();
        tagACIndex = Math.min(tagACIndex + 1, items.length - 1);
        updateACHighlight(items);
    } else if (e.key === 'ArrowUp' && visible) {
        e.preventDefault();
        tagACIndex = Math.max(tagACIndex - 1, 0);
        updateACHighlight(items);
    } else if (e.key === 'Enter') {
        e.preventDefault();
        if (visible && tagACIndex >= 0 && items[tagACIndex]) {
            // 자동완성 선택
            items[tagACIndex].dispatchEvent(new Event('mousedown'));
        } else {
            // 직접 입력한 텍스트를 칩으로
            addTagChip(e.target.value);
        }
    } else if (e.key === ',' || e.key === 'Tab') {
        const val = e.target.value.replace(',', '').trim();
        if (val) {
            e.preventDefault();
            addTagChip(val);
        }
    } else if (e.key === 'Backspace' && !e.target.value) {
        // 입력 비어있으면 마지막 칩 삭제
        const chips = document.getElementById('tagInputWrap').querySelectorAll('.tag-chip');
        if (chips.length) chips[chips.length - 1].remove();
    }
}

function updateACHighlight(items) {
    items.forEach((el, i) => el.classList.toggle('active', i === tagACIndex));
    if (items[tagACIndex]) items[tagACIndex].scrollIntoView({ block: 'nearest' });
}

function hideTagAC() {
    const acEl = document.getElementById('tagAutocomplete');
    if (acEl) { acEl.style.display = 'none'; acEl.innerHTML = ''; }
    tagACIndex = -1;
}

async function saveQaEdit(qaId) {
    const question = document.getElementById('editQuestion').value.trim();
    const answer   = document.getElementById('editAnswer').value.trim();
    const tags     = getTagChips();

    if (!question) { setQaEditMsg('질문을 입력하세요', false); return; }
    if (!answer)   { setQaEditMsg('답변을 입력하세요', false); return; }

    setQaEditMsg('저장 중...', null);
    document.querySelectorAll('#qaEditBody button').forEach(b => b.disabled = true);

    try {
        const res = await fetch(`/admin/qa/${encodeURIComponent(qaId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, answer, tags }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '저장 실패');

        setQaEditMsg('저장 완료 ✓', true);

        // 보기 모드 내용 즉시 갱신
        const viewBody = document.getElementById('qaViewBody');
        viewBody.querySelector('.detail-q').textContent = 'Q. ' + question;
        viewBody.querySelector('.detail-a').textContent = 'A. ' + answer;
        viewBody.querySelector('.tag-list').innerHTML =
            tags.map(t => `<span class="tag">#${escHtml(t)}</span>`).join('');

        // QA 목록 카드 질문 텍스트도 갱신
        const card = document.getElementById(`qa-card-${qaId}`);
        if (card) card.querySelector('.qa-q').textContent = question;

        setTimeout(() => {
            cancelQaEdit();
        }, 800);
    } catch(e) {
        setQaEditMsg(e.message, false);
    } finally {
        document.querySelectorAll('#qaEditBody button').forEach(b => b.disabled = false);
    }
}

function setQaEditMsg(msg, ok) {
    const el = document.getElementById('qaEditMsg');
    if (!el) return;
    el.textContent = msg;
    el.className = 'qa-edit-msg' + (ok === true ? ' ok' : ok === false ? ' err' : '');
}

function searchMenuForLink() {
    const q = document.getElementById('menuLinkSearch').value.trim().toLowerCase();
    const results = document.getElementById('menuSearchResults');
    if (!q) { results.innerHTML = ''; return; }

    const matched = allMenuItems.filter(m =>
        m.name.toLowerCase().includes(q) ||
        (m.menu_path || '').toLowerCase().includes(q)
    ).slice(0, 10);

    if (!matched.length) { results.innerHTML = '<div style="color:#999;font-size:12px">검색 결과 없음</div>'; return; }

    results.innerHTML = matched.map(m => `
        <div class="search-result-item">
            <span class="search-menu-path">📄 ${escHtml(m.menu_path || m.name)}</span>
            <button class="btn btn-success btn-sm"
                onclick="linkMenu('${escHtml(selectedQaId)}', '${escHtml(m.id)}', this)">연결</button>
        </div>`).join('');
}

async function linkCurrentMenu(qaId) {
    if (!selectedMenuItemId) return;
    await linkMenu(qaId, selectedMenuItemId, null);
}

async function linkMenu(qaId, menuItemId, btn) {
    if (!qaId) { alert('QA를 먼저 선택하세요'); return; }
    if (btn) { btn.disabled = true; btn.textContent = '...'; }
    try {
        const res = await fetch('/admin/qa-link', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ qa_id: qaId, menuitem_id: menuItemId })
        });
        if (!res.ok) throw new Error(await res.text());
        await renderDetail(qaId);
        updateMenuBadge(menuItemId, 1);
    } catch(e) {
        alert('연결 실패: ' + e.message);
        if (btn) { btn.disabled = false; btn.textContent = '연결'; }
    }
}

async function unlinkMenu(qaId, menuItemId, btn) {
    if (!confirm('이 연결을 해제하시겠습니까?')) return;
    btn.disabled = true;
    try {
        const res = await fetch('/admin/qa-link', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ qa_id: qaId, menuitem_id: menuItemId })
        });
        if (!res.ok) throw new Error(await res.text());
        await renderDetail(qaId);
        updateMenuBadge(menuItemId, -1);
    } catch(e) {
        alert('해제 실패: ' + e.message);
        btn.disabled = false;
    }
}

function updateMenuBadge(menuItemId, delta) {
    const item = allMenuItems.find(m => m.id === menuItemId);
    if (item) {
        item.qa_count = Math.max(0, (item.qa_count || 0) + delta);
        const row = document.getElementById(`menu-row-${menuItemId}`);
        if (row) {
            const badge = row.querySelector('.qa-badge');
            if (badge) {
                badge.textContent = item.qa_count;
                badge.className = `qa-badge ${item.qa_count > 0 ? 'has-qa' : 'no-qa'}`;
            }
        }
    }
}

// ─────────────────────────────────────────
// 검토 탭 (소스별 오탐 확인)
// ─────────────────────────────────────────
function populateReviewSourceSelect() {
    const src = document.getElementById('qaSource');
    const rev = document.getElementById('reviewSource');
    Array.from(src.options).forEach(opt => {
        if (opt.value) {
            const o = document.createElement('option');
            o.value = opt.value;
            o.textContent = opt.textContent;
            rev.appendChild(o);
        }
    });
    const votingOpt = Array.from(rev.options).find(o => o.value === '투표qna');
    if (votingOpt) { rev.value = '투표qna'; loadReview(); }
}

async function loadReview() {
    const source = document.getElementById('reviewSource').value;
    const listEl = document.getElementById('qaList');
    if (!source) {
        reviewData = [];
        listEl.innerHTML = '<div class="empty">소스를 선택하세요</div>';
        document.getElementById('reviewStats').textContent = '';
        return;
    }
    listEl.innerHTML = '<div class="loading-text">불러오는 중...</div>';
    try {
        const res = await fetch(`/admin/review?source=${encodeURIComponent(source)}&limit=300`);
        const data = await res.json();
        reviewData = data.items || [];
        renderReviewList();
    } catch(e) {
        listEl.innerHTML = '<div class="empty">로드 실패</div>';
    }
}

function isSuspicious(qa) {
    // 소스에 '투표'가 포함되는데 연결된 메뉴 경로에 '전자투표'/'투표'가 없으면 의심
    const srcHasVote = (qa.source || '').includes('투표') || qa.source === 'qna';
    return qa.menus.some(m => {
        const path = (m.path || m.main || '').toLowerCase();
        if (srcHasVote) {
            return !path.includes('전자투표') && !path.includes('투표');
        }
        return false;
    });
}

function renderReviewList() {
    const listEl = document.getElementById('qaList');
    const onlySuspicious = document.getElementById('reviewSuspiciousOnly')?.checked;

    let items = reviewData;
    if (onlySuspicious) {
        items = items.filter(qa => isSuspicious(qa));
    }

    const suspCount = reviewData.filter(qa => isSuspicious(qa)).length;
    document.getElementById('reviewStats').textContent =
        `연결 ${reviewData.length}개 / ⚠️ 의심 ${suspCount}개`;

    if (!items.length) {
        listEl.innerHTML = `<div class="empty">${onlySuspicious ? '의심 항목 없음 ✅' : '데이터 없음'}</div>`;
        return;
    }

    listEl.innerHTML = items.map(qa => {
        const susp = isSuspicious(qa);
        const menuRows = qa.menus.map(m => {
            const path = (m.path || m.name || '');
            const srcVote = (qa.source || '').includes('투표') || qa.source === 'qna';
            const warn = srcVote && !path.includes('전자투표') && !path.includes('투표');
            return `<div class="review-menu-row${warn ? ' warn' : ''}">
                ${warn ? '<span class="warn-icon">⚠️</span>' : '<span class="warn-icon">✅</span>'}
                <span class="review-menu-path${warn ? ' warn-text' : ''}">${escHtml(path)}</span>
                <button class="btn btn-danger btn-sm"
                    onclick="reviewUnlink('${escHtml(qa.id)}','${escHtml(m.id)}',this)">해제</button>
            </div>`;
        }).join('');

        return `<div class="review-card${susp ? ' suspicious' : ' clean'}" id="review-qa-${qa.id}">
            <div class="review-q">${susp ? '⚠️ ' : ''}${escHtml(qa.question)}</div>
            <div class="qa-meta" style="margin-bottom:4px">
                <span class="qa-source">${escHtml(qa.source || '')}</span>
                <span style="font-size:10px;color:#999">${escHtml(qa.id)}</span>
            </div>
            <div class="review-menus">${menuRows}</div>
        </div>`;
    }).join('');
}

async function reviewUnlink(qaId, menuItemId, btn) {
    if (!confirm('이 연결을 해제하시겠습니까?')) return;
    btn.disabled = true;
    try {
        const res = await fetch('/admin/qa-link', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ qa_id: qaId, menuitem_id: menuItemId })
        });
        if (!res.ok) throw new Error(await res.text());
        // 로컬 캐시 업데이트
        const qa = reviewData.find(q => q.id === qaId);
        if (qa) {
            qa.menus = qa.menus.filter(m => m.id !== menuItemId);
            if (qa.menus.length === 0) {
                reviewData = reviewData.filter(q => q.id !== qaId);
            }
        }
        renderReviewList();
        updateMenuBadge(menuItemId, -1);
    } catch(e) {
        alert('해제 실패: ' + e.message);
        btn.disabled = false;
    }
}

// ─────────────────────────────────────────
// 메뉴관계 탭 — 목록 렌더링 & 관계 관리
// ─────────────────────────────────────────

function renderRelList(side, items) {
    const listEl = document.getElementById(side === 'src' ? 'relSrcList' : 'relTgtList');
    const selId   = side === 'src' ? relSrcId : relTgtId;
    if (!items.length) {
        listEl.innerHTML = '<div class="empty">메뉴가 없습니다</div>';
        return;
    }
    // main_menu 기준 그룹핑
    const groups = {};
    items.forEach(m => {
        const key = m.main_menu || '기타';
        if (!groups[key]) groups[key] = [];
        groups[key].push(m);
    });

    let html = '';
    for (const main of Object.keys(groups).sort()) {
        html += `<div class="rel-menu-cat">📁 ${escHtml(main)}</div>`;
        groups[main].forEach(m => {
            const isSel = m.id === selId;
            const selClass = isSel ? ` sel-${side}` : '';
            const id   = escHtml(m.id);
            const cnt  = side === 'src' ? (relCountBySource[m.id] || 0) : 0;
            const right = (cnt > 0 ? `<span class="rel-count-badge" title="연결된 관계 ${cnt}개">${cnt}</span>` : '')
                        + (isSel ? '<span class="rel-check">✓</span>' : '');
            html += `<div class="rel-menu-item${selClass}"
                onclick="selectRelMenu('${side}','${id}')">
                <span>📄 ${escHtml(m.name)}</span>
                <span class="rel-item-right">${right}</span>
            </div>`;
        });
    }
    listEl.innerHTML = html;
}

function filterRelList(side) {
    const q = document.getElementById(side === 'src' ? 'relSrcFilter' : 'relTgtFilter')
                       .value.trim().toLowerCase();
    // 대상(tgt) 목록은 전역 대분류 필터를 적용하지 않음 — 관계는 대분류를 넘나들 수 있으므로 전체 메뉴 노출
    const main = side === 'src' ? currentMainFilter() : '';
    let filtered = relMenuItems;
    if (main) filtered = filtered.filter(m => (m.main_menu || '(없음)') === main);
    if (q) filtered = filtered.filter(m =>
            m.name.toLowerCase().includes(q) ||
            (m.menu_path || '').toLowerCase().includes(q) ||
            (m.main_menu || '').toLowerCase().includes(q));
    renderRelList(side, filtered);
}

function selectRelMenu(side, id) {
    const item = relMenuItems.find(m => m.id === id);
    const path = item ? (item.menu_path || item.name) : id;
    if (side === 'src') { relSrcId = id; relSrcPath = path; }
    else               { relTgtId = id; relTgtPath = path; }

    const displayEl = document.getElementById(side === 'src' ? 'relSrcDisplay' : 'relTgtDisplay');
    displayEl.textContent = path;

    // 현재 필터 유지하면서 목록 재렌더링 (선택 표시 갱신)
    filterRelList(side);
}

function updateRelTypeStyle() {
    const val = document.querySelector('input[name="relTypeRadio"]:checked')?.value || '관련메뉴';
    ['관련메뉴','선행업무','후속업무'].forEach(t => {
        const label = document.getElementById(`relTypeLabel-${t}`);
        if (!label) return;
        label.className = 'rel-radio-label' + (t === val
            ? (t === '관련메뉴' ? ' type-rel' : t === '선행업무' ? ' type-pre' : ' type-post')
            : '');
    });
}

async function addRelation() {
    if (!relSrcId) { alert('① 소스 메뉴를 먼저 선택하세요'); return; }
    if (!relTgtId) { alert('③ 대상 메뉴를 먼저 선택하세요'); return; }
    if (relSrcId === relTgtId) { alert('소스와 대상이 같은 메뉴입니다'); return; }

    const relType = document.querySelector('input[name="relTypeRadio"]:checked')?.value || '관련메뉴';
    const memo    = document.getElementById('relMemo').value.trim();
    const btn = document.querySelector('.rel-add-btn');
    btn.disabled = true; btn.textContent = '추가 중...';

    try {
        const res = await fetch('/admin/menu-relation', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_id: relSrcId, target_id: relTgtId, rel_type: relType, memo })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || '추가 실패');
        }
        // 선택 초기화
        relSrcId = null; relSrcPath = '';
        relTgtId = null; relTgtPath = '';
        document.getElementById('relSrcDisplay').textContent = '선택 안됨';
        document.getElementById('relTgtDisplay').textContent = '선택 안됨';
        document.getElementById('relSrcFilter').value = '';
        document.getElementById('relTgtFilter').value = '';
        document.getElementById('relMemo').value = '';
        filterRelList('src');
        filterRelList('tgt');
        await loadRelations();
    } catch(e) {
        alert('관계 추가 실패: ' + e.message);
    } finally {
        btn.disabled = false; btn.textContent = '➕ 관계 추가';
    }
}

async function loadRelations() {
    const tbody = document.getElementById('relTableBody');
    tbody.innerHTML = '<tr><td colspan="5" class="loading-text">로딩 중...</td></tr>';
    try {
        const res = await fetch('/admin/menu-relations');
        const data = await res.json();
        const items = data.items || [];
        document.getElementById('relCountBadge').textContent = `(${items.length}개)`;
        // 소스 메뉴별 연결 관계 수 집계 후 소스 목록 배지 갱신
        relCountBySource = {};
        items.forEach(r => { relCountBySource[r.source_id] = (relCountBySource[r.source_id] || 0) + 1; });
        if (document.getElementById('relSrcList')) filterRelList('src');
        if (!items.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty">등록된 관계가 없습니다</td></tr>';
            return;
        }
        tbody.innerHTML = items.map(r => {
            const badge = relTypeBadge(r.rel_type);
            const sid = escHtml(r.source_id), tid = escHtml(r.target_id), rt = escHtml(r.rel_type);
            return `<tr>
                <td style="font-weight:600">${escHtml(r.source_path || r.source_name)}</td>
                <td>${badge}</td>
                <td style="font-weight:600">${escHtml(r.target_path || r.target_name)}</td>
                <td class="rel-memo">${escHtml(r.memo || '')}</td>
                <td>
                    <button class="rel-del-btn"
                        onclick="deleteRelation('${sid}','${tid}','${rt}',this)">삭제</button>
                </td>
            </tr>`;
        }).join('');
    } catch(e) {
        tbody.innerHTML = `<tr><td colspan="5" class="empty">로드 실패</td></tr>`;
    }
}

function exportMenuMd() {
    const main = currentMainFilter();
    if (!main) {
        alert('상단 "대분류"에서 내보낼 대분류를 먼저 선택하세요.');
        return;
    }
    window.open('/admin/export/menu-md?main_menu=' + encodeURIComponent(main), '_blank');
}

function relTypeBadge(type) {
    if (type === '선행업무') return `<span class="rel-badge rel-badge-pre">→ 선행업무</span>`;
    if (type === '후속업무') return `<span class="rel-badge rel-badge-post">← 후속업무</span>`;
    return `<span class="rel-badge rel-badge-rel">↔ 관련메뉴</span>`;
}

async function deleteRelation(srcId, tgtId, rtype, btn) {
    if (!confirm('이 관계를 삭제하시겠습니까?')) return;
    btn.disabled = true; btn.textContent = '...';
    try {
        const res = await fetch('/admin/menu-relation', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_id: srcId, target_id: tgtId, rel_type: rtype, memo: '' })
        });
        if (!res.ok) throw new Error(await res.text());
        await loadRelations();
    } catch(e) {
        alert('삭제 실패: ' + e.message);
        btn.disabled = false; btn.textContent = '삭제';
    }
}

function escHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
        .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// 초기화
init();

// ─────────────────────────────────────────
// ─────────────────────────────────────────
// 메뉴설명 탭
// ─────────────────────────────────────────
let descAllItems = [];   // 전체 메뉴 목록 캐시
let descSelectedId = null;

async function loadDescMenuList() {
    try {
        const noDescOnly = document.getElementById('descNoDescOnly').checked;
        const res = await fetch(`/admin/menu-desc-list?no_desc_only=${noDescOnly}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        descAllItems = data.items || [];
        filterDescList();
    } catch(e) {
        console.error('메뉴설명 목록 로드 실패:', e);
        document.getElementById('descMenuList').innerHTML =
            '<div style="padding:20px;text-align:center;color:#E53935">목록 로드 실패</div>';
    }
}

function filterDescList() {
    const q = (document.getElementById('descSearch').value || '').trim().toLowerCase();
    const main = currentMainFilter();
    let filtered = descAllItems;
    if (main) filtered = filtered.filter(m => (m.main_menu || '(없음)') === main);
    if (q) filtered = filtered.filter(m => (m.name || '').toLowerCase().includes(q) || (m.menu_path || '').toLowerCase().includes(q));
    renderDescMenuList(filtered);
}

function renderDescMenuList(items) {
    const el = document.getElementById('descMenuList');
    if (!items.length) {
        el.innerHTML = '<div style="padding:20px;text-align:center;color:#aaa">항목 없음</div>';
        return;
    }
    // main_menu 그룹별 렌더링
    const groups = {};
    items.forEach(m => {
        const g = m.main_menu || '기타';
        if (!groups[g]) groups[g] = [];
        groups[g].push(m);
    });

    let html = '';
    Object.keys(groups).sort().forEach(g => {
        html += `<div class="desc-group-label">${escHtml(g)}</div>`;
        groups[g].forEach(m => {
            const hasDesc = m.description && m.description.trim().length > 0;
            const hasEmb  = m.has_embedding;
            const sel     = m.id === descSelectedId ? ' selected' : '';
            const menuName = m.menu_item || m.name || m.sub_menu || '';
            const subLabel = m.sub_menu ? `<span style="color:#999;font-size:11px"> ${escHtml(m.sub_menu)} &rsaquo;</span> ` : '';
            html += `
            <div class="desc-menu-item${sel}" onclick="selectDescMenu(event, '${m.id.replace(/'/g,"\\'")}')">
                <span class="desc-menu-name" title="${escHtml(m.menu_path||'')}">${subLabel}${escHtml(menuName)}</span>
                <span class="desc-badge ${hasDesc ? 'desc-badge-ok' : 'desc-badge-no'}">${hasDesc ? '설명✓' : '설명✗'}</span>
                ${hasEmb ? '<span class="desc-badge desc-badge-emb">임베딩</span>' : ''}
            </div>`;
        });
    });
    el.innerHTML = html;
}

function selectDescMenu(e, id) {
    descSelectedId = id;
    const item = descAllItems.find(m => m.id === id);
    const path = item ? (item.menu_path || '') : '';
    const description = item ? (item.description || '') : '';

    // 선택 하이라이트 갱신
    document.querySelectorAll('.desc-menu-item').forEach(el => el.classList.remove('selected'));
    e.currentTarget.classList.add('selected');

    document.getElementById('descEditorEmpty').style.display = 'none';
    document.getElementById('descEditorForm').style.display  = 'flex';
    document.getElementById('descEditorPath').textContent     = path;
    document.getElementById('descTextarea').value             = description;
    document.getElementById('descStatusMsg').textContent      = '';
    updateDescCharCount();
}

function updateDescCharCount() {
    const len = (document.getElementById('descTextarea').value || '').length;
    document.getElementById('descCharCount').textContent = `${len.toLocaleString()}자`;
}

async function saveDescription(withEmbed) {
    if (!descSelectedId) return;
    const desc = document.getElementById('descTextarea').value.trim();
    if (withEmbed && !desc) {
        showDescStatus('임베딩 생성에는 설명이 필요합니다', false);
        return;
    }

    const saveBtn      = document.getElementById('descSaveBtn');
    const saveEmbedBtn = document.getElementById('descSaveEmbedBtn');
    saveBtn.disabled = saveEmbedBtn.disabled = true;
    showDescStatus(withEmbed ? '임베딩 생성 중... (수초 소요)' : '저장 중...', null);

    try {
        const res = await fetch('/admin/menu-description', {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                menu_id: descSelectedId,
                description: desc,
                generate_embedding: withEmbed,
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '오류');
        const embMsg = data.has_embedding ? ' (임베딩 생성 완료)' : '';
        showDescStatus(`저장 완료${embMsg}`, true);
        // 목록 뱃지 갱신을 위해 캐시 업데이트
        const item = descAllItems.find(m => m.id === descSelectedId);
        if (item) {
            item.description   = desc;
            item.has_embedding = data.has_embedding || item.has_embedding;
        }
        // 현재 필터(대분류/검색)를 유지하며 목록 갱신
        filterDescList();
    } catch (e) {
        showDescStatus(e.message, false);
    } finally {
        saveBtn.disabled = saveEmbedBtn.disabled = false;
    }
}

function showDescStatus(msg, ok) {
    const el = document.getElementById('descStatusMsg');
    el.textContent  = msg;
    el.className    = 'desc-status-msg' + (ok === true ? ' desc-status-ok' : ok === false ? ' desc-status-err' : '');
}

// Driver.js 도움말 투어
// ─────────────────────────────────────────
function startQaTour() {
    const driverObj = window.driver.js.driver({
        showProgress: true,
        progressText: '{{current}} / {{total}}',
        nextBtnText: '다음 →',
        prevBtnText: '← 이전',
        doneBtnText: '완료',
        steps: [
            {
                element: '.panel-menu',
                popover: {
                    title: '① 메뉴 목록',
                    description: '관리할 메뉴를 선택하는 패널입니다.<br>검색어를 입력하거나 목록을 클릭해 메뉴를 선택하세요.',
                    side: 'right', align: 'start'
                }
            },
            {
                element: '#menuSearch',
                popover: {
                    title: '메뉴 검색',
                    description: '메뉴 이름 또는 경로의 일부를 입력하면 실시간으로 목록이 좁혀집니다.',
                    side: 'right', align: 'start'
                }
            },
            {
                element: '.panel-qa',
                popover: {
                    title: '② QA 목록',
                    description: '선택한 메뉴와 관련된 QA 항목들이 표시됩니다.<br>탭으로 <b>연결됨 / 미연결 / 검색 / 검토</b>를 전환할 수 있습니다.',
                    side: 'top', align: 'center'
                }
            },
            {
                element: '#tab-linked',
                popover: {
                    title: '연결됨 탭',
                    description: '현재 선택한 메뉴에 <b>이미 연결된 QA</b> 목록입니다.',
                    side: 'bottom', align: 'start'
                }
            },
            {
                element: '#tab-unlinked',
                popover: {
                    title: '미연결 탭',
                    description: '<b>어떤 메뉴에도 연결되지 않은 QA</b> 목록입니다.<br>소스 필터로 특정 파일만 볼 수 있습니다.',
                    side: 'bottom', align: 'start'
                }
            },
            {
                element: '#tab-search',
                popover: {
                    title: '검색 탭',
                    description: '질문·답변 키워드 또는 소스 파일 이름으로 <b>QA를 검색</b>합니다.',
                    side: 'bottom', align: 'start'
                }
            },
            {
                element: '#tab-review',
                popover: {
                    title: '검토 탭',
                    description: '소스별 연결 상태를 확인하여 <b>잘못 연결된 QA를 찾아 해제</b>합니다.<br>⚠️ 표시는 도메인 불일치 가능성을 나타냅니다.',
                    side: 'bottom', align: 'start'
                }
            },
            {
                element: '.panel-detail',
                popover: {
                    title: '③ QA 상세 / 편집',
                    description: 'QA 항목을 클릭하면 여기에서 질문·답변을 수정하고<br>메뉴 연결을 추가하거나 해제할 수 있습니다.',
                    side: 'left', align: 'start'
                }
            },
            {
                element: '.export-btn',
                popover: {
                    title: '📥 QA 내보내기',
                    description: '메뉴별로 연결된 QA를 <b>JSON 파일 묶음(ZIP)</b>으로 다운로드합니다.',
                    side: 'bottom', align: 'end'
                }
            }
        ]
    });
    driverObj.drive();
}

function startRelationTour() {
    const driverObj = window.driver.js.driver({
        showProgress: true,
        progressText: '{{current}} / {{total}}',
        nextBtnText: '다음 →',
        prevBtnText: '← 이전',
        doneBtnText: '완료',
        steps: [
            {
                element: '.rel-side-panel:first-child',
                popover: {
                    title: '① 소스 메뉴 선택',
                    description: '관계의 <b>기준(소스)</b>이 되는 메뉴를 클릭하여 선택하세요.<br>상단 필터로 메뉴를 빠르게 찾을 수 있습니다.',
                    side: 'right', align: 'start'
                }
            },
            {
                element: '.rel-mid-panel',
                popover: {
                    title: '② 관계 유형 설정',
                    description: '소스와 대상 메뉴 사이의 관계를 선택합니다.<br>· <b>↔ 관련메뉴</b>: 함께 참고할 메뉴<br>· <b>→ 선행업무</b>: 먼저 처리해야 할 메뉴<br>· <b>← 후속업무</b>: 이후에 이어지는 메뉴',
                    side: 'top', align: 'center'
                }
            },
            {
                element: '.rel-side-panel:last-child',
                popover: {
                    title: '③ 대상 메뉴 선택',
                    description: '관계의 <b>대상</b>이 되는 메뉴를 클릭하여 선택하세요.',
                    side: 'left', align: 'start'
                }
            },
            {
                element: '.rel-add-btn',
                popover: {
                    title: '④ 관계 추가',
                    description: '소스·유형·대상을 모두 선택한 뒤 이 버튼을 클릭하면<br>관계가 즉시 저장되고 챗봇 검색에 반영됩니다.',
                    side: 'top', align: 'center'
                }
            },
            {
                element: '.rel-bottom-area',
                popover: {
                    title: '⑤ 기존 관계 목록',
                    description: '등록된 모든 메뉴 관계를 확인할 수 있습니다.<br>우측 <b>✕</b> 버튼으로 관계를 삭제할 수 있습니다.',
                    side: 'top', align: 'center'
                }
            }
        ]
    });
    driverObj.drive();
}

function startDescTour() {
    const driverObj = window.driver.js.driver({
        showProgress: true,
        progressText: '{{current}} / {{total}}',
        nextBtnText: '다음 →',
        prevBtnText: '← 이전',
        doneBtnText: '완료',
        steps: [
            {
                element: '.desc-list-panel',
                popover: {
                    title: '① 메뉴 목록',
                    description: '설명을 입력할 메뉴를 선택하는 패널입니다.<br>검색어로 빠르게 찾을 수 있습니다.',
                    side: 'right', align: 'start'
                }
            },
            {
                element: '#descNoDescOnly',
                popover: {
                    title: '설명 없는 메뉴만 보기',
                    description: '체크하면 아직 설명이 입력되지 않은 메뉴만 표시합니다.<br><b>설명✗</b> 배지가 표시된 메뉴가 대상입니다.',
                    side: 'right', align: 'start'
                }
            },
            {
                element: '.desc-editor-panel',
                popover: {
                    title: '② 설명 편집기',
                    description: '좌측에서 메뉴를 클릭하면 여기에 설명을 입력할 수 있습니다.',
                    side: 'left', align: 'start'
                }
            },
            {
                element: '#descSaveBtn',
                popover: {
                    title: '💾 저장',
                    description: '설명 텍스트만 저장합니다.<br>임베딩 없이 저장하면 챗봇 검색에는 반영되지 않습니다.',
                    side: 'top', align: 'start'
                }
            },
            {
                element: '#descSaveEmbedBtn',
                popover: {
                    title: '✨ 저장 + 임베딩 생성',
                    description: '설명을 저장하고 <b>Gemini로 임베딩을 생성</b>합니다.<br>임베딩이 있어야 챗봇 벡터 검색에서 해당 메뉴가 검색됩니다.<br>수 초가 소요됩니다.',
                    side: 'top', align: 'start'
                }
            }
        ]
    });
    driverObj.drive();
}

function openHelp() {
    const activeTab = document.querySelector('.main-tab-btn.active');
    if (activeTab && activeTab.id === 'main-tab-relation') {
        startRelationTour();
    } else if (activeTab && activeTab.id === 'main-tab-desc') {
        startDescTour();
    } else {
        startQaTour();
    }
}
