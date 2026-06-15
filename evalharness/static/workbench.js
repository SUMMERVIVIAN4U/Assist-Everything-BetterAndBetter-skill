let report = null;
    let settings = null;
    let selectedCaseKey = null;
    let selectedEvalGroupKey = null;
    let selectedEvalRoundKey = null;
    let expandedEvalRoundKey = null;
    const expandedLedgerKeys = new Set();
    const evalGroupPages = new Map();
    let chatEvalReport = null;
    let performanceReport = null;
    let scenarios = [];
    let expandedScenarioId = 'GIFT';
    let currentSessionDirty = false;
    let currentSessionEvaled = false;
    let savedProvider = 'minimax';
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
      const desiredProvider = cfg.llm_provider || cfg.default_llm_provider || 'minimax';
      providerSelect.value = Array.from(providerSelect.options).some(option => option.value === desiredProvider)
        ? desiredProvider
        : 'minimax';
      savedProvider = providerSelect.value;
      const health = document.getElementById('llmHealth');
      if (cfg.llm_configured === false) {
        health.textContent = 'Provider 未配置，Agent Chat 和 Eval 会要求真实 LLM。';
      } else {
        health.textContent = `当前 Provider：${providerSelect.options[providerSelect.selectedIndex]?.textContent || providerSelect.value}`;
      }
    }
    async function fetchReport() {
      report = await (await fetch('/api/report')).json();
      renderCases();
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
      document.getElementById('settingsAgent').textContent = `llm_provider=${providerDisplayName(settings.llm_provider || settings.agent_mode || 'minimax')} · eval=real_llm_only`;
      document.getElementById('privacyItems').value = (settings.privacy_items || []).join('\n');
      document.getElementById('identityText').value = settings.persona?.identity || '';
      document.getElementById('soulText').value = settings.persona?.soul || '';
      renderConfigSummaries();
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
      if (id === 'memory') refreshVisibleMemoryStore();
    }
    function chatStageElement() {
      return document.getElementById('chatStage');
    }
    function openEvalDrawer(opts = {}) {
      const stage = chatStageElement();
      if (!stage) return;
      if (opts.hasEval) stage.classList.add('has-eval');
      stage.classList.add('eval-open');
    }
    function closeEvalDrawer() {
      const stage = chatStageElement();
      if (!stage) return;
      stage.classList.remove('eval-open');
    }
    function toggleEvalDrawer() {
      const stage = chatStageElement();
      if (!stage) return;
      stage.classList.toggle('eval-open');
    }
    function setEvalDrawerHasEval(hasEval) {
      const stage = chatStageElement();
      if (!stage) return;
      stage.classList.toggle('has-eval', !!hasEval);
    }
    function clearEvalDrawerState() {
      const stage = chatStageElement();
      if (!stage) return;
      stage.classList.remove('eval-open');
      stage.classList.remove('has-eval');
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
    function renderConfigSummaries() {
      const provider = settings?.llm_provider || settings?.agent_mode || 'minimax';
      document.getElementById('agentPersonaSummary').textContent = JSON.stringify({
        identity: '真实任务协作助手；不预设用户身份、性别或关系。',
        soul: '短、直接、像可靠朋友；默认交付方案，不在正文解释工具动作。',
        llm_provider: providerDisplayName(provider),
        eval: '真实 LLM chat + 真实 LLM judge'
      }, null, 2);
      document.getElementById('skillConfigSummary').textContent = JSON.stringify({
        runtime: 'Workbench Agent Chat 与 Eval 共用同一个 skill runtime',
        applies_to: ['记忆提取', '记忆召回', '删除/降级', '隐私过滤', 'LLM 回复改写保护'],
        config_source: '页面保存的配置会直接重建当前 Workbench agent，无需另配一套'
      }, null, 2);
    }
    function renderMemoryPolicySummary() {
      const rules = [
        ['写入', '先用规则捕捉预算、禁忌、已送历史、删除等高置信信号；再让 LLM 判断复杂语义，例如“复述候选名=选定”。只写用户说过、确认过或明确授权的信息。'],
        ['召回', '先按场景、主体和当前任务过滤，再分成 apply_now 和 confirm_first。apply_now 可直接使用；confirm_first 只提示“之前出现过，要不要继续适用”。'],
        ['分层', 'current_task 只服务当前 session；scene_memory 下次同类任务先确认；long_term 才默认长期生效。比如“这次父亲不去”不会变成永久事实。'],
        ['整理/降级', '用户否定、删除或临时覆盖时，旧记忆不会硬套。删除后的语义会进入 suppression，连近义活动也会被压住。'],
        ['演进', '用户纠错会沉淀成 workflow 或 scene_memory，例如“我重复方案名就是选定”。下次同类场景少问、少绕、少重复。']
      ];
      document.getElementById('memoryPolicySummary').innerHTML = rules.map(([title, body]) => `<div class="rule"><b>${title}</b><div class="muted">${body}</div></div>`).join('');
    }
    function renderEvalRulesSummary() {
      const rules = [
        ['六维得分', '真实 LLM judge 按 100 分评分：可复测 10、提取 20、应用 25、更新淘汰 20、透明度 10、结果质量 15。'],
        ['费力度', '每个 session 单独累计：用户每轮 +1；每 50 字 +1；被追问 +2；重复说明 +3；纠错 +3；严重错误 +5。'],
        ['记忆节省信息点', '只数“本轮正确复用、且用户没有重复说明”的信息点。比如预算、颜色、材质、已送历史、禁忌各算一个信息点。'],
        ['多轮比较', 'Round 1/2/3 分别新开 session 后 Eval。History Evals 聚合成一组，直接看总分、费力度、记忆节省信息点是否变好。']
      ];
      document.getElementById('evalRulesSummary').innerHTML = rules.map(([title, body]) => `<div class="rule"><b>${title}</b><div class="muted">${body}</div></div>`).join('');
    }
    async function savePersona() {
      const status = document.getElementById('personaStatus');
      status.textContent = 'saving...';
      const data = await (await fetch('/api/settings/persona', {
        method:'POST',
        headers:{'content-type':'application/json'},
        body:JSON.stringify({persona:{identity:document.getElementById('identityText').value, soul:document.getElementById('soulText').value}})
      })).json();
      if (!data.ok) {
        status.textContent = data.error || '保存失败';
        return;
      }
      settings = data.settings;
      renderConfigSummaries();
      status.textContent = '已保存，下一次 Agent 回复生效。';
    }
    function renderMemoryBackendSettings() {
      const backend = settings?.memory_backend || {};
      document.getElementById('memoryEnabled').checked = backend.memory_enabled !== false;
      document.getElementById('memoryBackend').value = backend.backend || 'local';
      previewMemoryBackendSelection();
      renderMemoryPolicySummary();
      renderEvalRulesSummary();
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
      fetchMemoryStore('local');
      fetchMemoryStore('mem0_hosted');
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
      renderConfigSummaries();
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
      return document.getElementById('llmProvider').value || 'minimax';
    }
    function providerDisplayName(provider) {
      const labels = {minimax:'MiniMax', deepseek_pro:'DeepSeek V4 Pro', deepseek_flash:'DeepSeek V4 Flash'};
      return labels[provider] || provider || 'MiniMax';
    }
    async function saveAgentProvider(opts = {}) {
      const el = document.getElementById('llmHealth');
      const provider = selectedProvider();
      const providerLabel = document.getElementById('llmProvider').options[document.getElementById('llmProvider').selectedIndex]?.textContent || provider;
      if (provider === savedProvider && opts.silent !== false) return;
      el.textContent = `正在切换 Provider：${providerLabel}`;
      try {
        const data = await (await fetch('/api/settings/agent', {
          method:'POST',
          headers:{'content-type':'application/json'},
          body:JSON.stringify({provider})
        })).json();
        if (!data.ok) {
          el.textContent = data.error || 'Provider 切换失败';
          return;
        }
        settings = data.settings;
        savedProvider = settings.llm_provider || provider;
        el.textContent = `当前 Provider：${providerLabel}`;
        renderConfigSummaries();
      } catch (err) {
        el.textContent = `Provider 切换失败 · ${err.message}`;
      }
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
      clearEvalDrawerState();
      appendMsg('user', text);
      currentSessionDirty = true;
      currentSessionEvaled = false;
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
      openEvalDrawer({hasEval: true});
      status.innerHTML = '<span class="loading-row"><span class="spinner"></span><span>正在统一 eval 当前会话...</span></span>';
      panel.innerHTML = '';
      try {
        chatEvalReport = await (await fetch('/api/run-chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({provider:selectedProvider()})})).json();
        if (chatEvalReport.ok === false) {
          throw new Error(chatEvalReport.error || '真实 LLM eval 失败');
        }
        report = chatEvalReport;
        const c = chatEvalReport.cases?.[0];
        status.textContent = c ? '完成：已生成六维评分。' : '没有可评估 eval。';
        panel.innerHTML = c ? renderEvalCase(c, {compact:true}) : '<div class="muted">当前对话为空。</div>';
        currentSessionEvaled = !!c;
        setEvalDrawerHasEval(true);
        renderCases();
        return currentSessionEvaled;
      } catch (err) {
        status.textContent = '打分失败。';
        panel.innerHTML = `<div class="note" style="color:var(--bad)">ERROR: ${escapeHtml(err.message)}</div>`;
        setEvalDrawerHasEval(true);
        return false;
      } finally {
        btn.disabled = false;
        btn.textContent = 'Run LLM Eval';
      }
    }
    function askSessionResetAction() {
      const modal = document.getElementById('sessionResetModal');
      if (!modal) {
        const shouldEval = window.confirm('上一个 Session 还没有 Eval。点击“确定”先 Eval，再新开 Session；点击“取消”留在当前 Session。');
        return Promise.resolve(shouldEval ? 'eval' : 'cancel');
      }
      modal.classList.remove('hidden');
      const buttons = Array.from(modal.querySelectorAll('[data-reset-choice]'));
      return new Promise(resolve => {
        const cleanup = choice => {
          buttons.forEach(button => button.removeEventListener('click', onClick));
          document.removeEventListener('keydown', onKeydown);
          modal.classList.add('hidden');
          resolve(choice);
        };
        const onClick = event => cleanup(event.currentTarget.dataset.resetChoice || 'cancel');
        const onKeydown = event => {
          if (event.key === 'Escape') cleanup('cancel');
        };
        buttons.forEach(button => button.addEventListener('click', onClick));
        document.addEventListener('keydown', onKeydown);
      });
    }
    async function resetSession(opts = {}) {
      let keepPreviousEval = false;
      if (!opts.force && currentSessionDirty && !currentSessionEvaled) {
        const action = await askSessionResetAction();
        if (action === 'cancel') return false;
        if (action === 'eval') {
          const evalOk = await runChatEval();
          if (!evalOk) {
            const skipEval = window.confirm('Eval 失败或没有可评估内容。是否跳过 Eval，直接新开 Session？');
            if (!skipEval) return false;
          } else {
            keepPreviousEval = true;
          }
        }
      }
      await fetch('/api/reset-chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({provider:selectedProvider()})});
      document.getElementById('chatlog').innerHTML = '';
      if (keepPreviousEval) {
        document.getElementById('chatEvalStatus').textContent = '上一轮 Eval 已完成并保存到 History Evals；Session 已重置。';
        openEvalDrawer({hasEval: true});
      } else {
        document.getElementById('chatEvalStatus').textContent = opts.reason || 'Session 已重置；memory 保持不变。';
        document.getElementById('chatEvalPanel').innerHTML = '暂无评分。';
        clearEvalDrawerState();
      }
      currentSessionDirty = false;
      currentSessionEvaled = false;
      refreshChatMemory();
      return true;
    }
    async function clearHistoryEvals() {
      if (!window.confirm('确定清空 History Evals？这会删除本地 eval 历史和 latest 报告。')) return;
      const data = await (await fetch('/api/history/clear', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({provider:selectedProvider()})})).json();
      report = data;
      selectedEvalGroupKey = null;
      selectedCaseKey = null;
      renderCases();
      document.getElementById('historyHint').textContent = 'History Evals 已清空。';
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
      clearEvalDrawerState();
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
            <button class="scenario-step ${step.action === 'eval' ? 'eval-step' : ''} ${step.new_session ? 'new-session-step' : ''}" onclick="runScenarioStep('${escapeAttr(scenario.id)}', ${index})">
              <span class="step-index">${index + 1}</span>
              <span><b>${escapeHtml(step.label)}</b><span>${escapeHtml(step.hint || step.text || (step.action === 'eval' ? '评估当前 session' : ''))}</span></span>
            </button>
          `).join('')}</div>
        ` : ''}
      </div>`;
    }
    function toggleScenario(id) {
      expandedScenarioId = expandedScenarioId === id ? '' : id;
      renderScenarioLibrary();
    }
    async function runScenarioStep(scenarioId, index) {
      const scenario = scenarios.find(item => item.id === scenarioId);
      const step = scenario?.steps?.[index];
      if (!step) return;
      if (step.action === 'eval') {
        await runChatEval();
        return;
      }
      if (step.new_session) {
        const resetOk = await resetSession({reason: `${step.label} 已新开 session；上一轮请在 History Evals 查看。`});
        if (!resetOk) return;
        appendMsg('assistant', `新 session 已开始。现在跑：${step.label}`);
      }
      fillScenarioStep(step.text || '');
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
      return out.sort((a, b) => new Date(b.run.created_at || 0) - new Date(a.run.created_at || 0));
    }
    function evalGroups() {
      const groups = new Map();
      historyCases().forEach(row => {
        const key = evalGroupKey(row.c);
        if (!groups.has(key)) groups.set(key, {key, name: evalGroupName(row.c), rows: []});
        groups.get(key).rows.push(row);
      });
      return Array.from(groups.values()).map(group => {
        group.rows.sort((a, b) => new Date(b.run.created_at || 0) - new Date(a.run.created_at || 0));
        group.latest = group.rows[0];
        return group;
      }).sort((a, b) => new Date(b.latest.run.created_at || 0) - new Date(a.latest.run.created_at || 0));
    }
    function evalGroupKey(c) {
      const text = `${c.title || ''} ${(c.script?.messages || []).join(' ')}`;
      if (/礼物|女朋友|首饰|生日/.test(text)) return 'gift';
      if (/旅行|行程|亲子|南京|上海|杭州|北京/.test(text)) return 'travel';
      if (/复习|考试|例题|番茄钟/.test(text)) return 'study';
      if (/综述|研究|RAG|文献/.test(text)) return 'research';
      return c.domain || c.id || 'chat';
    }
    function evalGroupName(c) {
      const key = evalGroupKey(c);
      const names = {gift:'女朋友生日礼物', travel:'家庭旅行规划', study:'考试复习计划', research:'文献综述与研究设计'};
      return names[key] || coreTaskName(c);
    }
    function coreTaskName(c) {
      const messages = c.script?.messages || [];
      const first = messages.find(text => text && !/展示当前记忆|reset memory/i.test(text)) || c.title || c.id || '';
      return first.length > 24 ? `${first.slice(0, 24)}...` : first;
    }
    function caseDisplayName(c) {
      return coreTaskName(c || {});
    }
    function renderCases() {
      const groups = evalGroups();
      const list = document.getElementById('caseList');
      if (!groups.length) {
        list.innerHTML = '<div class="history-empty"><b>暂无历史 Eval</b><div class="muted">运行一次 Agent Chat Eval 后会出现在这里。</div></div>';
        document.getElementById('caseDetail').innerHTML = '<div class="history-empty history-empty-detail"><b>暂无历史 Eval</b><div class="muted">在 Agent Chat 运行 Run LLM Eval 后，会按测试任务聚合显示在这里。</div></div>';
        return;
      }
      if (!selectedEvalGroupKey || !groups.find(group => group.key === selectedEvalGroupKey)) selectedEvalGroupKey = groups[0].key;
      const activeGroup = groups.find(group => group.key === selectedEvalGroupKey);
      if (activeGroup && (!selectedEvalRoundKey || !activeGroup.rows.find(row => row.key === selectedEvalRoundKey))) {
        selectedEvalRoundKey = activeGroup.latest.key;
      }
      list.innerHTML = groups.map(group => {
        const latest = group.latest;
        return `
        <button class="case-btn ${group.key === selectedEvalGroupKey ? 'active' : ''}" onclick="selectCase('${escapeAttr(group.key)}')">
          <div class="history-case-title">${escapeHtml(group.name)}</div>
          <div class="history-case-meta">Chat ${formatTime(chatStartedAt(latest.c)) || '-'}</div>
          <div class="history-case-meta">Eval ${formatTime(latest.run.created_at) || '-'}</div>
          <div class="history-case-rounds">${group.rows.length} 轮</div>
        </button>`;
      }).join('');
      const selected = groups.find(group => group.key === selectedEvalGroupKey);
      document.getElementById('caseDetail').innerHTML = renderEvalGroup(selected);
    }
    function selectCase(key) {
      selectedEvalGroupKey = key;
      selectedEvalRoundKey = null;
      expandedEvalRoundKey = null;
      expandedLedgerKeys.clear();
      renderCases();
    }
    function renderEvalGroup(group) {
      if (!group) return '暂无历史 eval。';
      const pageSize = 3;
      const pageCount = Math.max(1, Math.ceil(group.rows.length / pageSize));
      const page = Math.min(evalGroupPages.get(group.key) || 0, pageCount - 1);
      const start = page * pageSize;
      const visibleRows = group.rows.slice(start, start + pageSize);
      const end = Math.min(start + visibleRows.length, group.rows.length);
      return `
        <div class="history-group">
          <div class="eval-group-head">
            <div>
              <h2>${escapeHtml(group.name)}</h2>
              <div class="muted">最新 Eval ${formatTime(group.latest.run.created_at) || '-'} · Chat ${formatTime(chatStartedAt(group.latest.c)) || '-'} · 共 ${group.rows.length} 轮 · 第 ${page + 1} / ${pageCount} 页，每页 ${pageSize} 轮</div>
            </div>
          </div>
          <div class="eval-round-section-head">
            <h3>多轮对比</h3>
            <div class="muted">费力度越低越好；记忆节省信息点只统计正确复用且用户没有重复说明的信息。</div>
          </div>
          <div class="eval-round-board">${visibleRows.map((row, index) => renderEvalRoundCard(group, row, start, index)).join('')}</div>
          ${group.rows.length > pageSize ? `<div class="pager history-pager"><button onclick="changeEvalGroupPage('${escapeAttr(group.key)}', -1)" ${page <= 0 ? 'disabled' : ''}>上一页</button><span class="muted">第 ${page + 1} / ${pageCount} 页 · 当前显示 ${start + 1}-${end} / ${group.rows.length} 轮</span><button onclick="changeEvalGroupPage('${escapeAttr(group.key)}', 1)" ${page >= pageCount - 1 ? 'disabled' : ''}>下一页</button></div>` : ''}
        </div>`;
    }
    function renderEvalRoundCard(group, row, start, index) {
      const absoluteIndex = start + index;
      const chronologicalRound = group.rows.length - absoluteIndex;
      const isLatest = absoluteIndex === 0;
      const effort = row.c.user_effort || {};
      const expanded = expandedEvalRoundKey === row.key;
      return `<article class="eval-round-card ${expanded ? 'active' : ''}">
        <div class="eval-round-card-head">
          <div>
            <div class="eval-round-kicker">第 ${chronologicalRound} 轮${isLatest ? ' · 最新' : ''}</div>
            <h3>${escapeHtml(caseDisplayName(row.c))}</h3>
          </div>
          <div class="muted">Eval ${formatTime(row.run.created_at) || '-'}</div>
        </div>
        <div class="muted">Chat ${formatTime(chatStartedAt(row.c)) || '-'}</div>
        <div class="metric-stack">
          ${renderLedgerMetric(row, 'effort')}
          ${isLedgerExpanded(row.key, 'effort') ? renderLedgerPanel(row, 'effort') : ''}
          ${renderLedgerMetric(row, 'saved')}
          ${isLedgerExpanded(row.key, 'saved') ? renderLedgerPanel(row, 'saved') : ''}
        </div>
        <button class="eval-round-toggle" onclick="toggleEvalRoundDetail('${escapeAttr(row.key)}')">${expanded ? '收起详情' : '展开详情'}</button>
        ${expanded ? renderEvalRoundDetail(row) : ''}
      </article>`;
    }
    function ledgerKey(rowKey, type) {
      return `${rowKey}:${type}`;
    }
    function isLedgerExpanded(rowKey, type) {
      return expandedLedgerKeys.has(ledgerKey(rowKey, type));
    }
    function toggleLedgerPanel(rowKey, type) {
      const key = ledgerKey(rowKey, type);
      if (expandedLedgerKeys.has(key)) expandedLedgerKeys.delete(key);
      else expandedLedgerKeys.add(key);
      selectedEvalRoundKey = rowKey;
      renderCases();
    }
    function renderLedgerMetric(row, type) {
      const effort = row.c.user_effort || {};
      const isEffort = type === 'effort';
      const label = isEffort ? '费力度' : '记忆节省信息点';
      const value = isEffort ? (effort.final_score ?? '-') : memorySavingScore(effort);
      const expanded = isLedgerExpanded(row.key, type);
      return `<button class="ledger-trigger ${isEffort ? 'effort' : 'saved'}" aria-expanded="${expanded ? 'true' : 'false'}" onclick="toggleLedgerPanel('${escapeAttr(row.key)}', '${type}')">
        <span>${label}</span>
        <b>${value}</b>
      </button>`;
    }
    function renderLedgerPanel(row, type) {
      const timeline = row.c.eval_timeline || [];
      const isEffort = type === 'effort';
      const items = timeline.filter(t => {
        const effort = t.effort || {};
        if (isEffort) {
          return effort.before !== undefined || effort.delta !== undefined || effort.after !== undefined || t.evaluation?.explanation;
        }
        return effort.saved_before !== undefined || effort.saved_delta !== undefined || effort.saved_after !== undefined || (effort.memory_saving_points || []).length;
      });
      if (!items.length) {
        return `<div class="ledger-panel ${isEffort ? 'effort' : 'saved'}"><div class="muted">暂无逐轮累计说明。</div></div>`;
      }
      return `<div class="ledger-panel ${isEffort ? 'effort' : 'saved'}">
        ${items.map(t => isEffort ? renderEffortLedgerItem(t) : renderSavedLedgerItem(t)).join('')}
      </div>`;
    }
    function renderEffortLedgerItem(t) {
      const effort = t.effort || {};
      return `<div class="ledger-item">
        <div class="ledger-item-head"><b>${escapeHtml(t.turn_id || '-')}</b><span>${effort.before ?? '-'} → +${effort.delta ?? 0} → ${effort.after ?? '-'}</span></div>
        <div class="muted">${escapeHtml(t.evaluation?.explanation || '暂无本轮说明。')}</div>
      </div>`;
    }
    function renderSavedLedgerItem(t) {
      const effort = t.effort || {};
      const points = effort.memory_saving_points || [];
      return `<div class="ledger-item">
        <div class="ledger-item-head"><b>${escapeHtml(t.turn_id || '-')}</b><span>${effort.saved_before ?? '-'} → +${effort.saved_delta ?? 0} → ${effort.saved_after ?? '-'}</span></div>
        ${points.length ? `<div class="chips">${points.map(point => `<span class="chip good">${escapeHtml(point)}</span>`).join('')}</div>` : '<div class="muted">本轮无新增节省点。</div>'}
      </div>`;
    }
    function toggleEvalRoundDetail(rowKey) {
      expandedEvalRoundKey = expandedEvalRoundKey === rowKey ? null : rowKey;
      selectedEvalRoundKey = rowKey;
      renderCases();
    }
    function selectEvalRound(groupKey, rowKey) {
      selectedEvalGroupKey = groupKey;
      selectedEvalRoundKey = rowKey;
      expandedEvalRoundKey = expandedEvalRoundKey === rowKey ? null : rowKey;
      renderCases();
    }
    function changeEvalGroupPage(groupKey, delta) {
      const groups = evalGroups();
      const group = groups.find(item => item.key === groupKey);
      const pageCount = Math.max(1, Math.ceil((group?.rows.length || 0) / 3));
      const next = Math.max(0, Math.min((evalGroupPages.get(groupKey) || 0) + delta, pageCount - 1));
      evalGroupPages.set(groupKey, next);
      const firstRowOnPage = group?.rows[next * 3];
      if (firstRowOnPage) selectedEvalRoundKey = firstRowOnPage.key;
      expandedEvalRoundKey = null;
      expandedLedgerKeys.clear();
      renderCases();
    }
    function memorySavingScore(effort = {}) {
      return effort.memory_saving_points ?? effort.saved_score ?? effort.reduction ?? 0;
    }
    function renderEvalRoundDetail(row) {
      const c = row.c || {};
      const checks = c.checks || {};
      return `<div class="eval-round-detail">
        <div class="note">当前采用双账本：费力度和记忆节省信息点分别累计，不相互抵扣。节省信息点只统计被正确复用的信息，不把新增、删除或更新记忆本身当作节省。</div>
        <div class="dims history-dims">${Object.entries(c.scores || {}).filter(([k])=>k!=='total').map(([k,v]) => `<div class="dim"><span class="muted">${dimNames[k] || k}</span><b>${v} / ${dimMax[k] || '-'}</b></div>`).join('') || '<div class="muted">暂无六维拆分。</div>'}</div>
        <div class="chips">
          <span class="chip">任务交付 ${checks.delivered_task_turns || 0}/${checks.task_turns || 0}</span>
          <span class="chip">记忆动作 ${c.memory_events?.length || 0}</span>
          <span class="chip ${checks.semantic_violations ? 'bad' : 'good'}">语义违规 ${checks.semantic_violations || 0}</span>
          <span class="chip ${checks.repeated_memory_turns ? 'bad' : 'good'}">重复说明 ${checks.repeated_memory_turns || 0}</span>
        </div>
        <div class="turn-list history-turn-list">${(c.eval_timeline || []).map(timelineCard).join('') || '<div class="muted">暂无轨迹。</div>'}</div>
      </div>`;
    }
    function chatStartedAt(c) { return c.script?.chat_started_at || c.turns?.[0]?.user?.timestamp || ''; }
    function renderEvalCase(c, opts = {}) {
      const effort = c.user_effort || {};
      const checks = c.checks || {};
      const isCompact = !!opts.compact;
      if (isCompact) {
        const savingPoints = memorySavingScore(effort);
        return `
          <div class="chat-eval-compact">
            <div class="case-head"><div><h2>${escapeHtml(caseDisplayName(c))}</h2><div class="muted">${escapeHtml(c.module || c.domain || '')}</div></div></div>
            <div class="dims">${Object.entries(c.scores || {}).filter(([k])=>k!=='total').map(([k,v]) => `<div class="dim"><span class="muted">${dimNames[k] || k}</span><b>${v} / ${dimMax[k] || '-'}</b></div>`).join('') || '<div class="muted">暂无六维拆分。</div>'}</div>
            <div class="metrics chat-eval-ledgers">
              <div class="metric"><span class="muted">费力度</span><b>${effort.final_score ?? '-'}</b><div class="muted">越低越省力。</div></div>
              <div class="metric"><span class="muted">记忆节省信息点</span><b>${savingPoints}</b><div class="muted">正确复用且用户未重复说明的信息点。</div></div>
            </div>
            <div class="note">Agent Chat 当前展示六维评分、费力度和记忆节省信息点；多轮横向对比请到 History Evals 查看。</div>
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
