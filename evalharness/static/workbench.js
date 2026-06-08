let report = null;
    let settings = null;
    let selectedCaseKey = null;
    let chatEvalReport = null;
    const dimNames = {reproducibility:'可复测', memory_extraction:'提取', memory_application:'应用', update_and_decay:'更新淘汰', transparency:'透明', result_quality:'质量'};
    const dimMax = {reproducibility:10, memory_extraction:20, memory_application:25, update_and_decay:20, transparency:10, result_quality:15};

    async function fetchConfig() {
      const cfg = await (await fetch('/api/config')).json();
      document.getElementById('agentMode').value = cfg.agent_mode || 'local';
    }
    async function fetchReport() {
      report = await (await fetch('/api/report')).json();
      renderCases();
      renderStats();
    }
    async function fetchSettings() {
      settings = await (await fetch('/api/settings')).json();
      document.getElementById('settingsAgent').textContent = `agent_mode=${settings.agent_mode || ''}`;
      document.getElementById('soulMd').value = settings.soul_md || '';
      document.getElementById('settingsMemory').textContent = JSON.stringify(settings.workbench_memory || {}, null, 2);
      document.getElementById('privacyItems').value = (settings.privacy_items || []).join('\n');
      renderMemoryBackendSettings();
      renderPrivacySettings();
    }
    function setTab(id, el) {
      document.querySelectorAll('main section').forEach(s => s.classList.add('hidden'));
      document.getElementById(id).classList.remove('hidden');
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      el.classList.add('active');
      if (id === 'settings') fetchSettings();
    }
    function setSettingsTab(id, el) {
      document.querySelectorAll('.settings-view').forEach(view => view.classList.add('hidden'));
      document.getElementById('settingsView' + id[0].toUpperCase() + id.slice(1)).classList.remove('hidden');
      document.querySelectorAll('.settings-tab').forEach(tab => tab.classList.remove('active'));
      el.classList.add('active');
      if (id === 'mem0') fetchMem0Memory();
    }
    function privacyItemsFromInput() {
      return document.getElementById('privacyItems').value.split(/\n+/).map(item => item.trim()).filter(Boolean);
    }
    async function savePrivacyItems() {
      const status = document.getElementById('privacyStatus');
      status.textContent = 'saving...';
      const data = await (await fetch('/api/settings/privacy', {
        method:'POST',
        headers:{'content-type':'application/json'},
        body:JSON.stringify({privacy_items: privacyItemsFromInput()})
      })).json();
      if (!data.ok) {
        status.textContent = data.error || '保存失败';
        return;
      }
      settings = data.settings;
      document.getElementById('privacyItems').value = (settings.privacy_items || []).join('\n');
      renderPrivacySettings();
      status.textContent = '已保存';
    }
    function resetDefaultPrivacyItems() {
      document.getElementById('privacyItems').value = (settings?.default_privacy_items || []).join('\n');
      document.getElementById('privacyStatus').textContent = '默认项已填入，保存后生效';
      renderPrivacySettings({previewItems: privacyItemsFromInput()});
    }
    function renderPrivacySettings(opts = {}) {
      const items = opts.previewItems || settings?.privacy_items || [];
      document.getElementById('privacyChips').innerHTML = items.length
        ? items.map(item => `<span class="chip">${escapeHtml(item)}</span>`).join('')
        : '<span class="muted">暂无隐私项。</span>';
      document.getElementById('privacyReport').textContent = JSON.stringify(settings?.privacy_report || {}, null, 2);
    }
    function renderMemoryBackendSettings() {
      const backend = settings?.memory_backend || {};
      const mem0 = backend.mem0 || {};
      document.getElementById('memoryEnabled').checked = backend.memory_enabled !== false;
      document.getElementById('memoryBackend').value = backend.backend || 'local';
      document.getElementById('memoryBackendSummary').textContent = JSON.stringify({
        backend: backend.backend || 'local',
        memory_enabled: backend.memory_enabled !== false,
        mem0: {
          configured: !!(mem0.api_key_configured && mem0.endpoint_configured && mem0.user_configured),
          api_key_configured: !!mem0.api_key_configured
        }
      }, null, 2);
      document.getElementById('memoryBackendStatus').textContent = mem0.api_key_configured ? 'Mem0 API key 已配置' : 'Mem0 API key 未配置';
    }
    async function fetchMem0Memory() {
      const status = document.getElementById('mem0MemoryStatus');
      const target = document.getElementById('settingsMem0Memory');
      status.textContent = 'loading...';
      try {
        const data = await (await fetch('/api/mem0-memory')).json();
        if (!data.ok) {
          status.textContent = `Mem0 Memory 暂不可用：${data.error || data.stage || 'unknown'}`;
          target.textContent = JSON.stringify(data, null, 2);
          return;
        }
        status.textContent = `Mem0 Memory · ${data.count || 0} 条`;
        target.textContent = JSON.stringify(data, null, 2);
      } catch (err) {
        status.textContent = `Mem0 Memory 加载失败：${err.message}`;
        target.textContent = '{}';
      }
    }
    async function saveMemoryBackend() {
      const status = document.getElementById('memoryBackendStatus');
      status.textContent = 'saving...';
      const payload = {
        backend: document.getElementById('memoryBackend').value,
        memory_enabled: document.getElementById('memoryEnabled').checked
      };
      const data = await (await fetch('/api/settings/memory-backend', {
        method:'POST',
        headers:{'content-type':'application/json'},
        body:JSON.stringify(payload)
      })).json();
      if (!data.ok) {
        status.textContent = data.error || '保存失败';
        return;
      }
      settings = data.settings;
      renderMemoryBackendSettings();
      document.getElementById('settingsMemory').textContent = JSON.stringify(settings.workbench_memory || {}, null, 2);
      status.textContent = `已保存 · 记忆${settings.memory_backend?.memory_enabled === false ? '关闭' : '开启'} · ${settings.memory_backend?.backend || 'local'}`;
    }
    async function checkMem0Health() {
      const status = document.getElementById('memoryBackendStatus');
      status.textContent = 'checking Mem0...';
      const data = await (await fetch('/api/mem0-health')).json();
      status.textContent = data.ok ? `Mem0 OK · ${data.stage} · results=${data.result_count ?? 0}` : `Mem0 FAIL · ${data.stage} · ${data.error || ''}`;
    }
    async function runPresetCases() {
      const list = document.getElementById('caseList');
      list.innerHTML = '<div class="loading-row"><span class="spinner"></span><span>正在运行 preset evals...</span></div>';
      report = await (await fetch('/api/run-preset', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({judge:document.getElementById('judge').value, agent:document.getElementById('agentMode').value})})).json();
      selectedCaseKey = null;
      renderCases();
      renderStats();
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
    async function sendChat() {
      const input = document.getElementById('chatInput');
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      appendMsg('user', text);
      const thinking = appendMsg('assistant', '正在思考...');
      try {
        const data = await (await fetch('/api/chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({message:text, agent:document.getElementById('agentMode').value})})).json();
        updateMsg(thinking, data.error ? ('ERROR: ' + data.error) : data.turn.assistant.content, 'assistant');
        document.getElementById('chatMemory').textContent = JSON.stringify(data.memory || {}, null, 2);
        document.getElementById('chatEvalStatus').textContent = '对话已更新，当前评分需重新 Run Eval。';
      } catch (err) {
        updateMsg(thinking, 'ERROR: ' + err.message, 'assistant');
      }
    }
    async function runChatEval() {
      const btn = document.getElementById('chatEvalBtn');
      const status = document.getElementById('chatEvalStatus');
      const panel = document.getElementById('chatEvalPanel');
      btn.disabled = true;
      btn.textContent = 'Scoring...';
      status.innerHTML = '<span class="loading-row"><span class="spinner"></span><span>正在统一 eval 当前会话...</span></span>';
      panel.innerHTML = '';
      try {
        chatEvalReport = await (await fetch('/api/run-chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({judge:document.getElementById('judge').value})})).json();
        report = chatEvalReport;
        const c = chatEvalReport.cases?.[0];
        status.textContent = c ? `完成：${c.score}/100` : '没有可评估 eval。';
        panel.innerHTML = c ? renderEvalCase(c, {compact:true}) : '<div class="muted">当前对话为空。</div>';
        renderCases();
        renderStats();
      } catch (err) {
        status.textContent = '打分失败。';
        panel.innerHTML = `<div class="note" style="color:var(--bad)">ERROR: ${escapeHtml(err.message)}</div>`;
      } finally {
        btn.disabled = false;
        btn.textContent = 'Run Eval';
      }
    }
    async function resetSession() {
      await fetch('/api/reset-chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({agent:document.getElementById('agentMode').value})});
      document.getElementById('chatlog').innerHTML = '';
      document.getElementById('chatEvalStatus').textContent = 'Session 已重置；memory 保持不变。';
      document.getElementById('chatEvalPanel').innerHTML = '暂无评分。';
    }
    async function resetMemory() {
      const data = await (await fetch('/api/reset-memory', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({agent:document.getElementById('agentMode').value})})).json();
      appendMsg('assistant', data.response?.text || '已重置 memory。');
      document.getElementById('chatMemory').textContent = JSON.stringify(data.memory || {}, null, 2);
      const remote = data.mem0_reset;
      const remoteText = remote?.stage === 'delete_all'
        ? `Mem0 已删除 ${remote.deleted_count || 0}/${remote.found_count || 0} 条。`
        : 'Mem0 未启用或未配置。';
      document.getElementById('chatEvalStatus').textContent = `Memory 已重置；${remoteText} Run Eval 后刷新评分。`;
      fetchMem0Memory();
    }
    function appendMsg(role, content) {
      const div = document.createElement('div');
      div.className = 'msg ' + role;
      div.innerHTML = `<b>${role === 'user' ? 'user' : 'assistant'}</b><div class="content">${escapeHtml(content)}</div>`;
      document.getElementById('chatlog').appendChild(div);
      div.scrollIntoView({block:'end'});
      return div;
    }
    function updateMsg(div, content, role) {
      div.className = 'msg ' + role;
      div.innerHTML = `<b>${role === 'user' ? 'user' : 'assistant'}</b><div class="content">${escapeHtml(content)}</div>`;
      div.scrollIntoView({block:'end'});
    }
    function historyCases() {
      const out = [];
      const history = report?.history || (report ? [report] : []);
      history.forEach(run => (run.cases || []).forEach(c => out.push({run, c, key:`${run.run_id || 'latest'}::${c.id}`})));
      return out;
    }
    function renderCases() {
      const rows = historyCases();
      const list = document.getElementById('caseList');
      if (!rows.length) {
        list.innerHTML = '<div class="muted">暂无历史 eval。</div>';
        document.getElementById('caseDetail').innerHTML = '暂无历史 eval。';
        return;
      }
      if (!selectedCaseKey || !rows.find(r => r.key === selectedCaseKey)) selectedCaseKey = rows[0].key;
      list.innerHTML = rows.map(r => `
        <button class="case-btn ${r.key === selectedCaseKey ? 'active' : ''}" onclick="selectCase('${escapeAttr(r.key)}')">
          <div class="case-head"><b>${escapeHtml(r.c.id)} ${escapeHtml(r.c.title || '')}</b><span class="score">${r.c.score}</span></div>
          <div class="muted">${escapeHtml(r.run.source || r.run.harness?.eval_source || '')} · ${formatTime(r.run.created_at)}</div>
          <div class="chips"><span class="chip">费力度 ${r.c.user_effort?.final_score ?? '-'}</span><span class="chip good">节省 ${r.c.user_effort?.saved_score ?? 0}</span></div>
        </button>`).join('');
      const selected = rows.find(r => r.key === selectedCaseKey);
      document.getElementById('caseDetail').innerHTML = renderEvalCase(selected.c, {run:selected.run});
    }
    function selectCase(key) { selectedCaseKey = key; renderCases(); }
    function renderStats() {
      const rows = historyCases();
      const latest = report?.summary || {};
      document.getElementById('stats').innerHTML = `
        <div class="metrics">
          <div class="metric"><span class="muted">历史 eval 数</span><b>${rows.length}</b></div>
          <div class="metric"><span class="muted">最新平均分</span><b>${latest.config_average ?? '-'}</b></div>
          <div class="metric"><span class="muted">最新平均费力度</span><b>${latest.effort_average ?? '-'}</b></div>
        </div>
        <div class="panel" style="margin-top:12px"><h2>历史运行</h2>
          <div class="turn-list">${(report?.history || []).map(run => `<div class="turn-card"><div class="case-head"><b>${escapeHtml(run.run_id || '')}</b><span class="score">${run.summary?.config_average ?? '-'}</span></div><div class="muted">${escapeHtml(run.source || '')} · ${formatTime(run.created_at)}</div></div>`).join('') || '<div class="muted">暂无历史运行。</div>'}</div>
        </div>`;
    }
    function renderEvalCase(c, opts = {}) {
      const effort = c.user_effort || {};
      const checks = c.checks || {};
      return `
        <div>
          <div class="case-head"><div><h2>${escapeHtml(c.id || '')} ${escapeHtml(c.title || '')}</h2><div class="muted">${escapeHtml(c.module || c.domain || '')}</div></div><span class="score">${c.score ?? '-'}/100</span></div>
          <div class="metrics">
            <div class="metric"><span class="muted">六维总分</span><b>${c.score ?? '-'}</b><div class="muted">六个维度的综合质量分，越高越好。</div></div>
            <div class="metric"><span class="muted">用户费力度</span><b>${effort.final_score ?? '-'}</b><div class="muted">累计成本点数，越低越省力。</div></div>
            <div class="metric"><span class="muted">记忆节省</span><b>${effort.saved_score ?? effort.reduction ?? 0}</b><div class="muted">因应用/更新记忆预计少解释的成本。</div></div>
          </div>
          <div class="note">费力度按加法计算：用户轮数、输入长度、追问、重复说明、纠错、不满、语义违规都会加成本；记忆应用和记忆变化只计入“记忆节省”，不再把成本扣成负数。</div>
          <div class="dims">${Object.entries(c.scores || {}).filter(([k])=>k!=='total').map(([k,v]) => `<div class="dim"><span class="muted">${dimNames[k] || k}</span><b>${v} / ${dimMax[k] || '-'}</b></div>`).join('')}</div>
          <div class="chips">
            <span class="chip">任务交付 ${checks.delivered_task_turns || 0}/${checks.task_turns || 0}</span>
            <span class="chip">记忆动作 ${c.memory_events?.length || 0}</span>
            <span class="chip ${checks.semantic_violations ? 'bad' : 'good'}">语义违规 ${checks.semantic_violations || 0}</span>
            <span class="chip ${checks.repeated_memory_turns ? 'bad' : 'good'}">重复说明 ${checks.repeated_memory_turns || 0}</span>
          </div>
          <h3>统一轨迹</h3>
          <div class="turn-list">${(c.eval_timeline || []).map(timelineCard).join('') || '<div class="muted">暂无轨迹。</div>'}</div>
        </div>`;
    }
    function timelineCard(t) {
      const effort = t.effort || {};
      const memory = t.memory || {};
      const actions = memory.actions || [];
      const applied = memory.applied || [];
      return `<div class="turn-card">
        <div class="turn-meta"><b>${escapeHtml(t.turn_id || '')} · ${escapeHtml(t.stage || '')}</b><span class="chip">M ${escapeHtml(memory.snapshot_version || '')}</span></div>
        <div class="dialog">
          <div class="bubble user"><b>user</b><div class="body">${escapeHtml(t.user || '')}</div></div>
          <div class="bubble agent"><b>agent</b><div class="body">${escapeHtml(brief(t.assistant || '', 260))}</div></div>
        </div>
        <div class="subgrid">
          <div class="mini"><b>Memory</b><div class="muted">${escapeHtml(memory.explanation || '')}</div>${applied.map(m => `<div class="event"><span class="chip">${escapeHtml(m.type)}</span><div class="body">${escapeHtml(m.content)}</div></div>`).join('')}${actions.map(a => `<div class="event"><span class="chip">${escapeHtml(a.action)}</span><div class="body">${escapeHtml(a.detail || '')}</div></div>`).join('')}</div>
          <div class="mini"><b>费力度</b><div class="body">成本 ${effort.before ?? 0} → ${effort.after ?? 0}，本轮 +${effort.delta ?? 0}</div><div class="body">节省 ${effort.saved_before ?? 0} → ${effort.saved_after ?? 0}，本轮 +${effort.saved_delta ?? 0}</div><div class="muted">${escapeHtml(t.evaluation?.explanation || '')}</div></div>
        </div>
      </div>`;
    }
    function brief(text, max = 180) {
      const clean = String(text || '').replace(/\n{2,}/g, '\n').trim();
      return clean.length > max ? `${clean.slice(0, max)}...` : clean;
    }
    function formatTime(value) {
      if (!value) return '';
      try { return new Date(value).toLocaleString(); } catch { return value; }
    }
    function escapeAttr(str) { return String(str ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;'); }
    function escapeHtml(str) { return String(str ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }
    fetchConfig().then(fetchReport).then(fetchSettings);
