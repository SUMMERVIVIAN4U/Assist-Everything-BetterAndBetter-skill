let report = null;
    let selectedCaseId = null;
    let chatEvalReport = null;
    let abReport = null;
    const dimNames = {
      reproducibility:'可复测', memory_extraction:'提取', memory_application:'应用',
      update_and_decay:'更新淘汰', transparency:'透明', result_quality:'质量'
    };
    const dimMax = {
      reproducibility:10, memory_extraction:20, memory_application:25,
      update_and_decay:20, transparency:10, result_quality:15
    };
    async function fetchConfig() {
      const cfg = await (await fetch('/api/config')).json();
      document.getElementById('agentMode').value = cfg.agent_mode || 'local';
    }
    async function fetchReport() {
      report = await (await fetch('/api/report')).json();
      renderAll();
    }
    async function runPresetCases() {
      report = await (await fetch('/api/run-preset', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({judge:document.getElementById('judge').value, agent:document.getElementById('agentMode').value})})).json();
      renderAll();
    }
    async function checkMimoHealth() {
      const el = document.getElementById('llmHealth');
      el.textContent = 'checking...';
      try {
        const data = await (await fetch('/api/llm-health')).json();
        el.textContent = data.ok ? `Mimo OK · ${data.stage} · ${data.elapsed_ms}ms` : `Mimo FAIL · ${data.stage} · ${data.error}`;
      } catch (err) {
        el.textContent = `Mimo FAIL · ${err.message}`;
      }
    }
    async function runChatEval() {
      const btn = document.getElementById('chatEvalBtn');
      const status = document.getElementById('chatEvalStatus');
      const panel = document.getElementById('chatEvalPanel');
      btn.disabled = true;
      btn.textContent = 'Scoring...';
      status.innerHTML = '<span class="loading-row"><span class="spinner"></span><span>打分中，正在汇总当前 Agent Chat trace...</span></span>';
      panel.innerHTML = '<div class="note muted">评估完成前先保留当前对话，不切换视图。</div>';
      try {
        chatEvalReport = await (await fetch('/api/run-chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({judge:document.getElementById('judge').value})})).json();
        report = chatEvalReport;
        selectedCaseId = chatEvalReport.cases?.[0]?.id || selectedCaseId;
        renderAll();
        renderChatEvalPanel(chatEvalReport);
      } catch (err) {
        status.textContent = '打分失败。';
        panel.innerHTML = `<div class="note" style="color:var(--bad)">ERROR: ${escapeHtml(err.message)}</div>`;
      } finally {
        btn.disabled = false;
        btn.textContent = 'Run Eval';
      }
    }
    async function runGiftAB() {
      const btn = document.getElementById('abRunBtn');
      const status = document.getElementById('abStatus');
      const panel = document.getElementById('abPanel');
      btn.disabled = true;
      btn.textContent = 'Running...';
      status.innerHTML = '<span class="loading-row"><span class="spinner"></span><span>正在回放两轮礼物脚本...</span></span>';
      panel.innerHTML = '<div class="note muted">回放完成后展示两条线的 transcript、费力度分解和差异。</div>';
      try {
        abReport = await (await fetch('/api/run-ab-script', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({agent:'local'})})).json();
        renderABPanel(abReport);
      } catch (err) {
        status.textContent = 'A/B 回放失败。';
        panel.innerHTML = `<div class="note" style="color:var(--bad)">ERROR: ${escapeHtml(err.message)}</div>`;
      } finally {
        btn.disabled = false;
        btn.textContent = 'Run A/B';
      }
    }
    function setTab(id, el) {
      document.querySelectorAll('main section').forEach(s => s.classList.add('hidden'));
      document.getElementById(id).classList.remove('hidden');
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      el.classList.add('active');
    }
    function renderAll() { renderDashboard(); renderCases(); renderTrace(); }
    function renderDashboard() {
      const s = report.summary, h = report.harness;
      document.getElementById('dashboard').innerHTML = `
        <div class="grid">
          <div class="metric"><div class="muted">平均分</div><div class="num">${s.config_average}</div></div>
          <div class="metric"><div class="muted">Case 数</div><div class="num">${s.case_count}</div></div>
          <div class="metric"><div class="muted">全部 > 90</div><div class="num">${s.all_cases_above_90 ? 'YES' : 'NO'}</div></div>
          <div class="metric"><div class="muted">Agent / Judge</div><div class="num" style="font-size:16px">${h.agent_mode}<br>${h.judge_mode}</div></div>
        </div>`;
    }
    function renderCases() {
      if (!report.cases.length) { document.getElementById('cases').innerHTML = '<div class="muted">暂无 Case。</div>'; return; }
      if (!selectedCaseId || !report.cases.find(c => c.id === selectedCaseId)) selectedCaseId = report.cases[0].id;
      const selected = report.cases.find(c => c.id === selectedCaseId);
      const events = memoryEvents(selected);
      const effort = selected.user_effort || {final_score:100, reduction:0, turns:[], rules:[]};
      document.getElementById('cases').innerHTML = `
        <div class="case-layout">
          <aside class="case">
            <b>历史执行 Case</b>
            <div class="case-list">${report.cases.map(c => `
              <button class="case-btn ${c.id === selectedCaseId ? 'active' : ''}" onclick="selectCase('${c.id}')">
                <div class="case-btn-title"><span>${c.id} ${escapeHtml(c.title)}</span><span class="score">${c.score}</span></div>
                <div class="muted">${escapeHtml(c.module || c.domain || '')}</div>
              </button>`).join('')}</div>
            <div class="note muted">单个对话/执行节点不单独给综合总分；Case 或 Chat Session 完成后汇总六维总分。</div>
          </aside>
          <article class="case-detail">
            <section class="case-score-head">
              <div class="metric"><div class="muted">当前 Case 总分</div><div class="num">${selected.score}</div><div class="muted">${selected.id}</div></div>
              <div class="case">
                <div class="case-head"><div><h2 style="margin:0">Case 六维评分</h2><div class="muted">${selected.id} ${escapeHtml(selected.title)}</div></div></div>
                <div class="dims">${Object.entries(selected.scores).filter(([k])=>k!=='total').map(([k,v])=>`<div class="dim"><span class="muted">${dimNames[k]}</span><b>${v} / ${dimMax[k]}</b></div>`).join('')}</div>
              </div>
            </section>
            <div class="case-stats">
              <div class="metric"><div class="muted">记忆终态</div><div class="num">${finalVersion(selected)}</div></div>
              <div class="metric"><div class="muted">费力度下降</div><div class="num">85 → ${effort.final_score}</div></div>
              <div class="metric"><div class="muted">记忆动作</div><div class="num">${selected.memory_events?.length || 0}</div></div>
              <div class="metric"><div class="muted">触发记忆的用户句</div><div class="num">${events.length}</div></div>
            </div>
            <div class="note">${caseGoal(selected)}</div>
            <h3>对话 / 执行时间线</h3>
            ${dialogTimeline(selected)}
            <h3>触发记忆变化的用户句</h3>
            <div class="event-list">${events.length ? events.map(eventCard).join('') : '<div class="muted">当前 case 未触发记忆变化。</div>'}</div>
            <h3>用户费力度趋势</h3>
            <div class="note">规则：分数越低越省力。纠错、重复说明、情绪反馈、违反记忆组合会升高费力度；有效交付、正确应用记忆、有效记忆变化会降低费力度。</div>
            <div class="event-list">${(effort.turns || []).map(effortTurnCard).join('')}</div>
          </article>
        </div>`;
    }
    function selectCase(id) { selectedCaseId = id; renderCases(); }
    function caseGoal(c) {
      if (c.id === 'CHAT-SESSION') return '评估当前 Agent Chat 对话是否形成、应用并透明展示记忆。';
      return `验证 ${escapeHtml(c.module || c.title)} 在 reset、连续任务、记忆变化和删除复测中的表现。`;
    }
    function finalVersion(c) {
      const snaps = c.snapshots || [];
      return snaps.length ? (snaps[snaps.length - 1].version || '-') : '-';
    }
    function dialogTimeline(c) {
      return `<div class="dialog-list">${(c.turns || []).map(t => `
        <div class="dialog-turn dialog-user"><div class="case-head"><b>user · ${escapeHtml(t.stage || 'chat')}</b><span class="chip">${t.memory_snapshot?.version || ''}</span></div><div class="field-body">${escapeHtml(t.user?.content || '')}</div></div>
        <div class="dialog-turn dialog-agent"><div class="case-head"><b>agent</b><span class="chip">${turnBadge(t)}</span></div><div class="chips">${turnActions(t).map(x => `<span class="chip">${escapeHtml(x)}</span>`).join('')}</div><div class="field-body">${escapeHtml(brief(t.assistant?.content || ''))}</div></div>
      `).join('')}</div>`;
    }
    function turnBadge(t) {
      const actions = (t.tool_calls || []).flatMap(call => call.output?.memory_actions || []).filter(a => a.action !== 'reset');
      if (actions.length) return `记忆变化 ${actions.length}`;
      if ((t.applied_memories || []).length) return `应用 ${(t.applied_memories || []).length} 条`;
      return '普通回复';
    }
    function turnActions(t) {
      const names = (t.tool_calls || []).map(call => call.name);
      const actions = (t.tool_calls || []).flatMap(call => call.output?.memory_actions || []).filter(a => a.action !== 'reset').map(a => a.action);
      const applied = (t.applied_memories || []).length ? ['retrieve_memory'] : [];
      return [...new Set([...names, ...actions, ...applied])].slice(0, 4);
    }
    function brief(text, max = 180) {
      const clean = String(text || '').replace(/\n{2,}/g, '\n').trim();
      return clean.length > max ? `${clean.slice(0, max)}...` : clean;
    }
    function memoryEvents(c) {
      const output = [];
      let previous = 'M0';
      (c.turns || []).forEach(t => {
        const actions = (t.tool_calls || []).flatMap(call => call.output?.memory_actions || []);
        actions.filter(a => a.action !== 'reset').forEach(a => {
          output.push({
            transition: `${previous} → ${a.version || t.memory_snapshot?.version || ''}`,
            action: a.action,
            detail: a.detail || '',
            user: t.user?.content || '',
            gain: transitionGain(a)
          });
          previous = a.version || previous;
        });
      });
      return output;
    }
    function eventCard(e) {
      return `<div class="event-card">
        <div class="event-version">${escapeHtml(e.transition)}</div>
        <div>
          <div><b>${escapeHtml(e.action)}</b> · ${escapeHtml(e.detail)}</div>
          <div class="muted">触发语：${escapeHtml(e.user)}</div>
          <div class="gain"><b>跃迁增益：</b>${escapeHtml(e.gain)}</div>
        </div>
      </div>`;
    }
    function transitionGain(action) {
      const text = action.detail || '';
      if (action.action === 'delete') return '后续检索会过滤这条记忆，避免旧偏好继续影响输出。';
      if (action.action === 'downgrade') return '旧规则降权或条件化，减少冲突场景下的误用。';
      if (text.includes('预算')) return '后续任务不再追问预算，可直接过滤不合适选项。';
      if (text.includes('紫色') || text.includes('喜欢')) return '后续推荐能主动命中对象偏好，减少用户纠错。';
      if (text.includes('一个') || text.includes('简洁')) return '后续输出会收敛，减少用户筛选成本。';
      return '这条记忆会在后续相似任务中减少重复说明。';
    }
    function effortTrend(c) {
      const events = memoryEvents(c);
      const versions = ['M0', ...events.map(e => e.transition.split('→').pop().trim())].slice(0, 4);
      if (!versions.length) return [{version:'M0', score:85, level:'高', reason:'空白状态，需要完整说明偏好和边界。'}];
      return versions.map((version, idx) => {
        const score = Math.max(18, 85 - idx * 20);
        const level = score >= 70 ? '高' : (score >= 40 ? '中' : '低');
        const reason = idx === 0 ? '空白状态，需要完整说明偏好和边界。' : (idx === versions.length - 1 ? '关键偏好已沉淀，用户只需提出任务或例外。' : '部分偏好已知，仍需补充边界。');
        return {version, score, level, reason};
      });
    }
    function effortTurnCard(e) {
      const cls = e.delta > 0 ? 'var(--bad)' : 'var(--accent)';
      return `<div class="event-card">
        <div class="event-version">${escapeHtml(e.turn_id || '')}<br><span style="color:${cls}">${e.delta > 0 ? '+' : ''}${e.delta}</span><br>${e.before} → ${e.after}</div>
        <div>
          <div><b>${escapeHtml(e.stage || '')}</b></div>
          <div class="muted">用户：${escapeHtml(e.user || '')}</div>
          <div class="field-body">原因：${(e.reasons || []).map(escapeHtml).join('；')}</div>
          <div class="chips">${(e.six_dim_gain || []).map(x => `<span class="chip">${escapeHtml(x)}</span>`).join('')}</div>
        </div>
      </div>`;
    }
    function stateTransition(turns) {
      const versions = turns.map(t => t.memory_snapshot?.version).filter(Boolean);
      if (!versions.length) return '无 snapshot';
      return versions[0] === versions[versions.length - 1] ? versions[0] : `${versions[0]} → ${versions[versions.length - 1]}`;
    }
    function memoryJourney(c) {
      const snaps = c.snapshots || [];
      if (!snaps.length) return [{name:'无快照', desc:'当前 case 没有 memory snapshot。', meta:''}];
      const pick = [snaps[0], snaps[Math.floor((snaps.length - 1)/2)], snaps[snaps.length - 1]];
      return pick.map((s, i) => ({
        name: s.version || `S${i}`,
        desc: `active ${s.active?.length || 0} / superseded ${s.superseded?.length || 0} / deleted ${s.deleted?.length || 0}`,
        meta: i === 0 ? '起点' : (i === 1 ? '中段' : '终态')
      }));
    }
    function renderTrace() {
      document.getElementById('trace').innerHTML = report.cases.map(c => `
        <div class="trace">
          <h3>${c.id} ${c.title} <span class="score">${c.score}/100</span></h3>
          ${c.turns.map(t=>`<details><summary>${t.id} · ${t.stage} · tools: ${t.tool_calls.map(x=>x.name).join(', ') || 'none'}</summary><pre>${escapeHtml(JSON.stringify(t, null, 2))}</pre></details>`).join('')}
        </div>`).join('');
    }
    function renderChatEvalPanel(data) {
      const status = document.getElementById('chatEvalStatus');
      const panel = document.getElementById('chatEvalPanel');
      const c = data?.cases?.[0];
      if (!c) {
        status.textContent = '没有可展示的评分结果。';
        panel.innerHTML = '<div class="muted">当前对话为空或 eval 未返回 case。</div>';
        return;
      }
      const effort = c.user_effort || {final_score:100, reduction:0, turns:[]};
      const checks = c.checks || {};
      status.textContent = `完成：${escapeHtml(c.id)} · ${escapeHtml(c.title || '')}`;
      panel.innerHTML = `
        <div class="compact-stats">
          <div class="mini-metric"><span class="muted">总分</span><b class="score">${c.score}</b></div>
          <div class="mini-metric"><span class="muted">费力度</span><b>${effort.final_score}</b></div>
          <div class="mini-metric"><span class="muted">下降</span><b>${effort.reduction}</b></div>
        </div>
        <div class="dims">${Object.entries(c.scores || {}).filter(([k])=>k!=='total').map(([k,v])=>`<div class="dim"><span class="muted">${dimNames[k] || k}</span><b>${v} / ${dimMax[k] || '-'}</b></div>`).join('')}</div>
        <div class="chips">
          <span class="chip">任务 ${checks.delivered_task_turns || 0}/${checks.task_turns || 0}</span>
          <span class="chip">记忆动作 ${c.memory_events?.length || 0}</span>
          <span class="chip">语义违规 ${checks.semantic_violations || 0}</span>
          <span class="chip">污染记忆 ${checks.polluted_memories || 0}</span>
        </div>
        ${c.judge?.fallback_error ? `<div class="note" style="color:var(--warn)">远端 judge 失败，已回退到 offline judge：${escapeHtml(c.judge.fallback_error)}</div>` : ''}
        <h3>费力度轨迹</h3>
        <div class="event-list">${(effort.turns || []).map(effortTurnCard).join('') || '<div class="muted">暂无费力度轨迹。</div>'}</div>
      `;
    }
    function renderABPanel(data) {
      const status = document.getElementById('abStatus');
      const panel = document.getElementById('abPanel');
      if (!data?.summary) {
        status.textContent = '没有可展示的 A/B 结果。';
        panel.innerHTML = '<div class="muted">A/B runner 未返回 summary。</div>';
        return;
      }
      const s = data.summary;
      status.textContent = `完成：第二轮费力度节省 ${s.second_session_effort_saved}，总轮数节省 ${s.turns_saved}，winner=${s.winner}`;
      panel.innerHTML = `
        <div class="compact-stats">
          <div class="mini-metric"><span class="muted">Memory 费力度</span><b>${s.memory_user_effort}</b></div>
          <div class="mini-metric"><span class="muted">Baseline 费力度</span><b>${s.baseline_user_effort}</b></div>
          <div class="mini-metric"><span class="muted">第二轮节省</span><b>${s.second_session_effort_saved}</b></div>
        </div>
        <div class="note">规则：分数越低越省力。主要看用户轮数、重复解释、纠错、缺记忆追问和违反已知约束；第二轮如果还要用户重复“上次选了什么”，会明确加成本。</div>
        <div class="ab-grid">
          ${abColumn(data.memory)}
          ${abColumn(data.baseline)}
        </div>
        <h3>费力度计算规则</h3>
        <div class="event-list">${(data.rules || []).map(rule => `<div class="event-card"><div class="event-version">${escapeHtml(rule.weight)}</div><div><b>${escapeHtml(rule.name)}</b><div class="field-body">${escapeHtml(rule.description)}</div></div></div>`).join('')}</div>
      `;
    }
    function abColumn(path) {
      const effort = path.effort || {};
      return `<div class="ab-column">
        <div class="case-head">
          <div><b>${escapeHtml(path.label || '')}</b><div class="muted">${escapeHtml(path.description || '')}</div></div>
          <span class="score">${effort.score ?? '-'}</span>
        </div>
        <div class="chips">
          <span class="chip">用户轮数 ${effort.user_turns ?? 0}</span>
          <span class="chip">重复解释 ${effort.repeated_explanations ?? 0}</span>
          <span class="chip">追问 ${effort.clarification_asks ?? 0}</span>
          <span class="chip">违规 ${effort.violations ?? 0}</span>
        </div>
        <div class="ab-turns">${abTurns(path.turns || [])}</div>
        <h3>费力度轨迹</h3>
        <div class="event-list">${(effort.trace || []).map(abEffortCard).join('')}</div>
      </div>`;
    }
    function abTurns(turns) {
      return turns.map(t => `
        <div class="ab-turn ab-user"><b>user · ${escapeHtml(t.script_session || '')}</b><div class="field-body">${escapeHtml(t.user?.content || '')}</div></div>
        <div class="ab-turn ab-agent"><b>agent</b><div class="chips">${turnActions(t).map(x => `<span class="chip">${escapeHtml(x)}</span>`).join('')}</div><div class="field-body">${escapeHtml(brief(t.assistant?.content || '', 220))}</div></div>
      `).join('');
    }
    function abEffortCard(e) {
      return `<div class="event-card">
        <div class="event-version">${escapeHtml(e.session || '')}<br>${escapeHtml(e.turn_id || '')}<br>+${e.delta}<br>${e.before} → ${e.after}</div>
        <div>
          <div class="muted">用户：${escapeHtml(e.user || '')}</div>
          <div class="field-body">原因：${(e.reasons || []).map(escapeHtml).join('；')}</div>
        </div>
      </div>`;
    }
    async function sendChat() {
      const input = document.getElementById('chatInput');
      const text = input.value.trim(); if (!text) return;
      input.value = '';
      appendMsg('user', text);
      const thinking = appendMsg('assistant thinking', '正在思考...');
      try {
        const data = await (await fetch('/api/chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({message:text, agent:document.getElementById('agentMode').value})})).json();
        updateMsg(thinking, data.error ? ('ERROR: ' + data.error) : data.turn.assistant.content, 'assistant');
        document.getElementById('chatMemory').textContent = JSON.stringify(data.memory, null, 2);
        markChatEvalStale();
      } catch (err) {
        updateMsg(thinking, 'ERROR: ' + err.message, 'assistant');
      }
    }
    async function resetSession() {
      await fetch('/api/reset-chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({agent:document.getElementById('agentMode').value})});
      document.getElementById('chatlog').innerHTML = '';
      chatEvalReport = null;
      document.getElementById('chatEvalStatus').textContent = 'Session 已重置；memory 保持不变。点击 Run Eval 后在这里显示当前 Agent Chat 的评分结果。';
      document.getElementById('chatEvalPanel').innerHTML = '暂无评分。';
    }
    async function resetMemory() {
      const data = await (await fetch('/api/reset-memory', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({agent:document.getElementById('agentMode').value})})).json();
      document.getElementById('chatMemory').textContent = JSON.stringify(data.memory || {}, null, 2);
      appendMsg('assistant', data.response?.text || '已重置 memory。');
      chatEvalReport = null;
      document.getElementById('chatEvalStatus').textContent = 'Memory 已重置；当前 session 对话未清空。点击 Run Eval 后刷新评分。';
      document.getElementById('chatEvalPanel').innerHTML = '暂无评分。';
    }
    function appendMsg(role, content) {
      const div = document.createElement('div');
      div.className = 'msg ' + role;
      const label = role.includes('user') ? 'user' : 'assistant';
      div.innerHTML = `<b>${label}</b><div class="content">${escapeHtml(content)}</div>`;
      document.getElementById('chatlog').appendChild(div);
      div.scrollIntoView({block:'end'});
      return div;
    }
    function updateMsg(div, content, role) {
      div.className = 'msg ' + role;
      const label = role.includes('user') ? 'user' : 'assistant';
      div.innerHTML = `<b>${label}</b><div class="content">${escapeHtml(content)}</div>`;
      div.scrollIntoView({block:'end'});
    }
    function markChatEvalStale() {
      if (!chatEvalReport) return;
      document.getElementById('chatEvalStatus').textContent = '对话已更新，当前评分已过期。重新点击 Run Eval 后刷新结果。';
    }
    function escapeHtml(str) { return String(str ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }
    fetchConfig().then(fetchReport);
