let report = null;
    let settings = null;
    let selectedCaseKey = null;
    let chatEvalReport = null;
    let performanceReport = null;
    let scenarios = [];
    let expandedScenarioId = 'GIFT';
    const dimNames = {reproducibility:'可复测', memory_extraction:'提取', memory_application:'应用', update_and_decay:'更新淘汰', transparency:'透明', result_quality:'质量'};
    const dimMax = {reproducibility:10, memory_extraction:20, memory_application:25, update_and_decay:20, transparency:10, result_quality:15};
    const memoryBackendLabels = {local:'本地JSON', mem0_hosted:'Mem0 Hosted'};
    const memoryBackendCheckLabels = {local:'无需 Check Mem0', mem0_hosted:'Check Mem0 Hosted'};

    async function fetchConfig() {
      const cfg = await (await fetch('/api/config')).json();
      const providerSelect = document.getElementById('llmProvider');
      if (Array.isArray(cfg.providers) && cfg.providers.length) {
        providerSelect.innerHTML = cfg.providers.map(provider => `
          <option value="${escapeAttr(provider.value)}">${escapeHtml(provider.label)}${provider.configured ? '' : ' · 未配置'}</option>
        `).join('');
      }
      providerSelect.value = cfg.llm_provider || cfg.default_llm_provider || 'deepseek_pro';
      const health = document.getElementById('llmHealth');
      if (cfg.llm_configured === false) {
        health.textContent = 'Provider 未配置，Agent Chat 和 Eval 会要求真实 LLM。';
      }
    }
    async function fetchReport() {
      report = await (await fetch('/api/report')).json();
      renderCases();
      renderStats();
    }
    async function fetchScenarios() {
      try {
        const data = await (await fetch('/api/scenarios')).json();
        scenarios = data.items || [];
        renderScenarioLibrary(data.run_hint || '');
      } catch (err) {
        document.getElementById('scenarioLibrary').innerHTML = `<div class="note" style="color:var(--bad)">测试案例加载失败：${escapeHtml(err.message)}</div>`;
      }
    }
    async function fetchSettings() {
      settings = await (await fetch('/api/settings')).json();
      document.getElementById('settingsAgent').textContent = `llm_provider=${settings.llm_provider || settings.agent_mode || 'deepseek_pro'} · eval=real_llm_only`;
      document.getElementById('privacyItems').value = (settings.privacy_items || []).join('\n');
      renderMemoryBackendSettings();
      renderChatMemory(settings.current_memory);
      renderPrivacySettings();
    }
    function setTab(id, el) {
      document.querySelectorAll('main section').forEach(s => s.classList.add('hidden'));
      document.getElementById(id).classList.remove('hidden');
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      el.classList.add('active');
      if (id === 'settings') fetchSettings();
      if (id === 'performance') fetchPerformanceLatest();
    }
    function setSettingsTab(id, el) {
      document.querySelectorAll('.settings-view').forEach(view => view.classList.add('hidden'));
      document.getElementById('settingsView' + id[0].toUpperCase() + id.slice(1)).classList.remove('hidden');
      document.querySelectorAll('.settings-tab').forEach(tab => tab.classList.remove('active'));
      el.classList.add('active');
      const memoryStores = {localMemory:'local', mem0Hosted:'mem0_hosted'};
      if (memoryStores[id]) fetchMemoryStore(memoryStores[id]);
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
      document.getElementById('memoryEnabled').checked = backend.memory_enabled !== false;
      document.getElementById('memoryBackend').value = backend.backend || 'local';
      previewMemoryBackendSelection();
    }
    function selectedMemoryBackend() {
      return document.getElementById('memoryBackend').value || settings?.memory_backend?.backend || 'local';
    }
    function previewMemoryBackendSelection() {
      const stored = settings?.memory_backend || {};
      const mem0 = stored.mem0 || {};
      const backend = selectedMemoryBackend();
      const memoryEnabled = document.getElementById('memoryEnabled').checked;
      const checkButton = document.getElementById('checkMem0Button');
      document.getElementById('memoryBackendSummary').textContent = JSON.stringify({
        backend,
        backend_label: memoryBackendLabels[backend] || backend,
        saved_backend: stored.backend || 'local',
        memory_enabled: memoryEnabled,
        mem0: {
          configured: !!(mem0.api_key_configured && mem0.endpoint_configured && mem0.user_configured),
          api_key_configured: !!mem0.api_key_configured
        }
      }, null, 2);
      checkButton.textContent = memoryBackendCheckLabels[backend] || 'Check Mem0';
      checkButton.disabled = backend === 'local';
      const savedSuffix = backend !== (stored.backend || 'local') ? ' · 待保存' : '';
      document.getElementById('memoryBackendStatus').textContent = backend === 'local'
        ? `本地JSON 已选择${savedSuffix}`
        : `${memoryBackendLabels[backend] || backend} ${mem0.api_key_configured ? 'API key 已配置' : 'API key 未配置'}${savedSuffix}`;
    }
    function renderChatMemory(currentMemory) {
      const payload = currentMemory || {};
      const enabled = payload.memory_enabled !== false;
      const engine = payload.engine_label || payload.selected_engine || '本地JSON';
      const selected = payload.selected_engine || 'local';
      const content = payload.content || {};
      const count = (selected === 'mem0' || selected === 'mem0_hosted')
        ? (content.count ?? (Array.isArray(content.memories) ? content.memories.length : 0))
        : ((content.active || []).length || 0);
      const suffix = selected === 'mem0'
        ? (content.ok === false ? ` · ${content.stage || 'unavailable'}` : ` · ${count} 条`)
        : selected === 'mem0_hosted'
        ? (content.ok === false ? ` · ${content.stage || 'unavailable'}` : ` · ${count} 条`)
        : ` · active ${count} 条`;
      document.getElementById('chatMemoryStatus').textContent = `记忆功能：${enabled ? '开启' : '关闭'} · 当前引擎：${engine}${suffix}`;
      document.getElementById('chatMemory').textContent = JSON.stringify(content, null, 2);
    }
    async function refreshChatMemory() {
      const status = document.getElementById('chatMemoryStatus');
      status.textContent = 'loading...';
      try {
        const data = await (await fetch('/api/current-memory')).json();
        renderChatMemory(data);
      } catch (err) {
        status.textContent = `当前 Memory 加载失败：${err.message}`;
      }
    }
    function memoryStoreElements(engine) {
      return {
        local: {status:'localMemoryStatus', target:'settingsLocalMemory', label:'本地Memory'},
        mem0_hosted: {status:'mem0HostedMemoryStatus', target:'settingsMem0HostedMemory', label:'Mem0 Hosted'}
      }[engine];
    }
    function memoryStoreEndpoint(engine) {
      return {
        local: '/api/memory-store?engine=local',
        mem0_hosted: '/api/memory-store?engine=mem0_hosted'
      }[engine];
    }
    async function fetchMemoryStore(engine) {
      const view = memoryStoreElements(engine);
      if (!view) return;
      const status = document.getElementById(view.status);
      const target = document.getElementById(view.target);
      status.textContent = 'loading...';
      try {
        const data = await (await fetch(memoryStoreEndpoint(engine))).json();
        const content = data.content || {};
        if (!data.ok || content.ok === false) {
          const error = content.error || data.error || content.stage || data.stage || 'unknown';
          status.textContent = `${view.label} 暂不可用：${error}`;
          target.textContent = JSON.stringify(data, null, 2);
          return;
        }
        const count = engine === 'local'
          ? ((content.active || []).length || 0)
          : (content.count ?? (Array.isArray(content.memories) ? content.memories.length : 0));
        status.textContent = `${view.label} · ${engine === 'local' ? 'active ' : ''}${count} 条`;
        target.textContent = JSON.stringify(data, null, 2);
      } catch (err) {
        status.textContent = `${view.label} 加载失败：${err.message}`;
        target.textContent = '{}';
      }
    }
    function refreshVisibleMemoryStore() {
      const visible = Array.from(document.querySelectorAll('.settings-view')).find(view => !view.classList.contains('hidden'));
      const stores = {
        settingsViewLocalMemory: 'local',
        settingsViewMem0Hosted: 'mem0_hosted'
      };
      if (visible && stores[visible.id]) fetchMemoryStore(stores[visible.id]);
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
      renderChatMemory(settings.current_memory);
      refreshVisibleMemoryStore();
      status.textContent = `已保存 · 记忆${settings.memory_backend?.memory_enabled === false ? '关闭' : '开启'} · ${settings.memory_backend?.backend || 'local'}`;
    }
    async function checkMem0Health() {
      const status = document.getElementById('memoryBackendStatus');
      const engine = selectedMemoryBackend();
      const label = memoryBackendLabels[engine] || engine;
      if (engine === 'local') {
        status.textContent = '本地JSON 无需 Check Mem0';
        return;
      }
      status.textContent = `checking ${label}...`;
      const data = await (await fetch(`/api/mem0-health?engine=${encodeURIComponent(engine)}`)).json();
      status.textContent = data.ok ? `${label} OK · ${data.stage} · results=${data.result_count ?? 0}` : `${label} FAIL · ${data.stage} · ${data.error || ''}`;
    }
    async function fetchPerformanceLatest() {
      const status = document.getElementById('performanceStatus');
      status.textContent = 'loading latest report...';
      try {
        const data = await (await fetch('/api/mem0-performance-demo/latest')).json();
        performanceReport = data;
        renderPerformanceReport(data);
      } catch (err) {
        status.textContent = `加载失败：${err.message}`;
      }
    }
    async function runPerformanceDemo() {
      const btn = document.getElementById('performanceRunBtn');
      const status = document.getElementById('performanceStatus');
      const mode = document.getElementById('performanceMode').value;
      const engine = document.getElementById('performanceEngine').value;
      if (mode === 'real_run' && engine !== 'local' && !window.confirm('Real Run 会向隔离 demo 用户写入大量 Mem0 记忆，完成后会尝试清理。确认继续？')) return;
      btn.disabled = true;
      btn.textContent = 'Running...';
      status.innerHTML = '<span class="loading-row"><span class="spinner"></span><span>正在运行性能演示...</span></span>';
      try {
        const payload = {
          engine,
          mode,
          scale: Number(document.getElementById('performanceScale').value),
          query_count: Number(document.getElementById('performanceQueries').value)
        };
        const data = await (await fetch('/api/mem0-performance-demo/run', {
          method:'POST',
          headers:{'content-type':'application/json'},
          body:JSON.stringify(payload)
        })).json();
        performanceReport = data;
        renderPerformanceReport(data);
      } catch (err) {
        status.textContent = `运行失败：${err.message}`;
      } finally {
        btn.disabled = false;
        btn.textContent = 'Run Demo';
      }
    }
    async function resetPerformanceDemo() {
      const status = document.getElementById('performanceStatus');
      status.textContent = 'resetting demo memory...';
      try {
        const payload = {engine: document.getElementById('performanceEngine').value};
        const data = await (await fetch('/api/mem0-performance-demo/reset', {
          method:'POST',
          headers:{'content-type':'application/json'},
          body:JSON.stringify(payload)
        })).json();
        status.textContent = data.ok
          ? `Demo Memory 已重置 · 删除 ${data.deleted_count || 0}/${data.found_count || 0} 条`
          : `Demo Memory 重置失败 · ${data.stage || ''} · ${data.error || (data.errors || []).join('; ')}`;
      } catch (err) {
        status.textContent = `重置失败：${err.message}`;
      }
    }
    function renderPerformanceReport(data) {
      const status = document.getElementById('performanceStatus');
      const metrics = document.getElementById('performanceMetrics');
      const timeline = document.getElementById('performanceTimeline');
      const examples = document.getElementById('performanceExamples');
      const raw = document.getElementById('performanceReport');
      raw.textContent = JSON.stringify(data || {}, null, 2);
      if (!data || data.ok === false) {
        const error = data?.error || (data?.errors || []).join('; ') || 'No performance demo has run yet.';
        status.textContent = `${data?.stage || 'empty'} · ${error}`;
        metrics.innerHTML = '';
        timeline.innerHTML = '';
        examples.innerHTML = '<div class="muted">暂无演示结果。</div>';
        return;
      }
      status.textContent = `${data.mode} · ${data.engine} · ${data.scale} 条 · demo_user=${data.demo_user_id}`;
      metrics.innerHTML = [
        metricHtml('Write QPS', data.metrics?.write_qps),
        metricHtml('Search P50', `${data.metrics?.search_p50_ms ?? '-'} ms`),
        metricHtml('Search P95', `${data.metrics?.search_p95_ms ?? '-'} ms`),
        metricHtml('Error Rate', data.metrics?.error_rate ?? 0)
      ].join('');
      timeline.innerHTML = (data.phases || []).map(phase => `
        <div class="timeline-step ${phase.ok ? 'good' : 'bad'}">
          <b>${escapeHtml(phase.name || '')}</b>
          <span>${phase.elapsed_ms ?? 0} ms</span>
          <span class="muted">count=${phase.count ?? '-'}</span>
        </div>`).join('');
      examples.innerHTML = (data.examples || []).map(example => `
        <div class="turn-card">
          <div class="case-head"><b>${escapeHtml(example.query || '')}</b><span class="chip">${example.latency_ms ?? 0} ms</span></div>
          <div class="turn-list">${(example.top_k || []).map(item => `
            <div class="mini">
              <div class="case-head"><b>${escapeHtml(item.id || '')}</b><span class="score">${item.retrieval_score ?? item.score ?? '-'}</span></div>
              <div class="body">${escapeHtml(item.content || '')}</div>
              <div class="muted">${escapeHtml(item.scope || '')} · ${escapeHtml(item.updated_at || '')} · ${escapeHtml(item.retrieval_rank_strategy || '')}</div>
            </div>`).join('') || '<div class="muted">无匹配结果。</div>'}</div>
        </div>`).join('') || '<div class="muted">暂无检索样例。</div>';
    }
    function metricHtml(label, value) {
      return `<div class="metric"><span class="muted">${escapeHtml(label)}</span><b>${escapeHtml(value ?? '-')}</b></div>`;
    }
    function safeJsonParse(text, fallback = {}) {
      try { return JSON.parse(text); } catch { return fallback; }
    }
    function selectedProvider() {
      return document.getElementById('llmProvider').value || 'deepseek_pro';
    }
    async function checkProviderHealth() {
      const el = document.getElementById('llmHealth');
      el.textContent = 'checking...';
      try {
        const data = await (await fetch(`/api/llm-health?provider=${encodeURIComponent(selectedProvider())}`)).json();
        el.textContent = data.ok
          ? `Provider OK · ${data.label || data.provider} · ${data.model} · ${data.elapsed_ms}ms`
          : `Provider FAIL · ${data.label || data.provider || ''} · ${data.stage} · ${data.error}`;
      } catch (err) {
        el.textContent = `Provider FAIL · ${err.message}`;
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
        const data = await (await fetch('/api/chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({message:text, provider:selectedProvider()})})).json();
        updateMsg(thinking, data.error ? ('ERROR: ' + data.error) : data.turn.assistant.content, 'assistant');
        renderChatMemory(data.current_memory || {content:data.memory || {}});
        document.getElementById('chatEvalStatus').textContent = '对话已更新，当前评分需重新 Run LLM Eval。';
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
        chatEvalReport = await (await fetch('/api/run-chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({provider:selectedProvider()})})).json();
        if (chatEvalReport.ok === false) {
          throw new Error(chatEvalReport.error || '真实 LLM eval 失败');
        }
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
        btn.textContent = 'Run LLM Eval';
      }
    }
    async function resetSession() {
      await fetch('/api/reset-chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({provider:selectedProvider()})});
      document.getElementById('chatlog').innerHTML = '';
      document.getElementById('chatEvalStatus').textContent = 'Session 已重置；memory 保持不变。';
      document.getElementById('chatEvalPanel').innerHTML = '暂无评分。';
      refreshChatMemory();
    }
    async function resetMemory() {
      const data = await (await fetch('/api/reset-memory', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({provider:selectedProvider()})})).json();
      appendMsg('assistant', data.response?.text || '已重置 memory。');
      renderChatMemory(data.current_memory || {content:data.memory || {}});
      const remote = data.mem0_reset;
      const remoteText = remote?.stage === 'delete_all'
        ? `Mem0 已删除 ${remote.deleted_count || 0}/${remote.found_count || 0} 条。`
        : remote?.action === 'reset'
        ? `当前记忆引擎已重置${remote.ok === false ? '失败' : ''}。`
        : 'Mem0 未启用或未配置。';
      document.getElementById('chatEvalStatus').textContent = `Memory 已重置；${remoteText} Run LLM Eval 后刷新评分。`;
      refreshVisibleMemoryStore();
    }
    function renderScenarioLibrary(runHint = '') {
      const target = document.getElementById('scenarioLibrary');
      if (!scenarios.length) {
        target.innerHTML = '<div class="muted">暂无测试案例。</div>';
        return;
      }
      const optimizedCount = scenarios.filter(item => item.optimized).length;
      document.getElementById('scenarioStatus').textContent = runHint || `${scenarios.length} 个测试案例，其中 ${optimizedCount} 个是近期针对性优化场景。`;
      target.innerHTML = scenarios.map(scenario => scenarioCard(scenario)).join('');
    }
    function scenarioCard(scenario) {
      const expanded = scenario.id === expandedScenarioId;
      const notes = scenario.optimization_notes || [];
      return `<div class="scenario-card ${expanded ? 'active' : ''}">
        <button class="scenario-title" onclick="toggleScenario('${escapeAttr(scenario.id)}')">
          <span>
            <b>${escapeHtml(caseDisplayName(scenario))}</b>
            <span class="muted">${escapeHtml(scenario.module || scenario.domain || '')}</span>
          </span>
          <span class="chip ${scenario.optimized ? 'good' : ''}">${scenario.optimized ? '已优化' : '基线'}</span>
        </button>
        ${expanded ? `
          <div class="scenario-notes">${notes.map(note => `<span class="chip good">${escapeHtml(note)}</span>`).join('') || '<span class="muted">通用 preset case。</span>'}</div>
          <div class="scenario-steps">${(scenario.steps || []).map((step, index) => `
            <button class="scenario-step" onclick="fillScenarioStep('${escapeAttr(step.text)}')">
              <span class="step-index">${index + 1}</span>
              <span><b>${escapeHtml(step.label)}</b><span>${escapeHtml(step.text)}</span></span>
            </button>
          `).join('')}</div>
        ` : ''}
      </div>`;
    }
    function toggleScenario(id) {
      expandedScenarioId = expandedScenarioId === id ? '' : id;
      renderScenarioLibrary();
    }
    function fillScenarioStep(text) {
      const input = document.getElementById('chatInput');
      input.value = text;
      input.focus();
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
    function caseDisplayName(c) {
      return c?.title || c?.name || c?.id || '';
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
          <div class="case-head"><b>${escapeHtml(caseDisplayName(r.c))}</b><span class="score">${r.c.score}</span></div>
          <div class="muted">${escapeHtml(r.run.source || r.run.harness?.eval_source || '')} · ${formatTime(r.run.created_at)}</div>
          <div class="chips"><span class="chip">费力度 ${r.c.user_effort?.final_score ?? '-'}</span><span class="chip good">记忆节省信息点 ${r.c.user_effort?.memory_saving_points ?? r.c.user_effort?.saved_score ?? 0}</span></div>
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
          <div class="metric"><span class="muted">平均记忆节省信息点</span><b>${latest.memory_saving_points_average ?? latest.saved_effort_average ?? '-'}</b></div>
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
          <div class="case-head"><div><h2>${escapeHtml(caseDisplayName(c))}</h2><div class="muted">${escapeHtml(c.module || c.domain || '')}</div></div><span class="score">${c.score ?? '-'}/100</span></div>
          <div class="metrics">
            <div class="metric"><span class="muted">六维总分</span><b>${c.score ?? '-'}</b><div class="muted">六个维度的综合质量分，越高越好。</div></div>
            <div class="metric"><span class="muted">费力度</span><b>${effort.final_score ?? '-'}</b><div class="muted">每轮用户输入、补充、重复说明、纠错和严重错误累计的沟通成本。</div></div>
            <div class="metric"><span class="muted">记忆节省信息点</span><b>${effort.memory_saving_points ?? effort.saved_score ?? effort.reduction ?? 0}</b><div class="muted">本轮回答正确复用、且用户没有重复说明的记忆信息点数量。</div></div>
          </div>
          <div class="note">当前采用双账本：费力度和记忆节省信息点分别累计，不相互抵扣。节省信息点只统计被正确复用的信息，不把新增、删除或更新记忆本身当作节省。</div>
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
          <div class="mini"><b>费力度 / 记忆节省信息点</b><div class="body">费力度 ${effort.before ?? 0} → ${effort.after ?? 0}，本轮 +${effort.delta ?? 0}</div><div class="body">记忆节省信息点 ${effort.saved_before ?? 0} → ${effort.saved_after ?? 0}，本轮 +${effort.saved_delta ?? 0}</div>${memorySavingPointsHtml(effort)}<div class="muted">${escapeHtml(t.evaluation?.explanation || '')}</div></div>
        </div>
      </div>`;
    }
    function memorySavingPointsHtml(effort) {
      const points = effort.memory_saving_points || [];
      if (!points.length) return '';
      return `<div class="chips">${points.map(point => `<span class="chip good">${escapeHtml(point)}</span>`).join('')}</div>`;
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
    fetchConfig().then(fetchScenarios).then(fetchReport).then(fetchSettings);
