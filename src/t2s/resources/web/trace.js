'use strict';

const tracePanel = (() => {
  const panel = document.getElementById('trace-panel');
  const stageLabel = document.getElementById('trace-stage');
  const list = document.getElementById('trace-events');
  const message = document.getElementById('trace-message');
  const history = document.getElementById('trace-history');
  const refresh = document.getElementById('trace-refresh');
  const more = document.getElementById('trace-more');
  const raw = document.getElementById('trace-raw');

  const titles = {
    session_created: '会话已创建',
    session_restored: '已读取会话记录',
    revision_conflict: '确认卡提交被拦截',
    planner_started: '开始理解问题',
    planner_finished: '已生成候选计划',
    planner_failed: '规划失败',
    plan_validated: '已检查查询计划',
    plan_rejected: '查询计划被拒绝',
    clarification_required: '等待补充条件',
    approval_required: '等待确认',
    workflow_resumed: '继续处理会话',
    choice_applied: '已应用补充选项',
    approval_accepted: '已确认查询',
    session_cancelled: '查询已取消',
    sql_compiled: '已准备查询语句',
    sql_started: '开始查询数据库',
    sql_finished: '查询完成',
    sql_failed: '数据库查询失败',
    sql_skipped: '已拦截重复执行',
  };
  const stages = {
    clarify: '待补充', confirm: '待确认', done: '已完成',
    cancelled: '已取消', reject: '已拒绝', error: '处理失败',
  };
  const fieldNames = { metric: '指标口径', date: '日期', dimension: '分组', category: '品类' };
  const actionNames = { choose: '提交选择', reply: '补充说明', confirm: '确认查询', cancel: '取消查询' };
  const statusLabels = { waiting: '等待', blocked: '已拦截', error: '失败' };
  const statuses = new Set(['recorded', 'started', 'success', 'waiting', 'blocked', 'error']);
  const timestamp = new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });

  const stageNames = {
    ...stages, pending: '等待规划', checking: '正在校验', approved: '已批准',
  };
  const categoryNames = {
    session: '会话', model: '模型', validation: '校验',
    human: '人工操作', query: '查询准备', sql: '数据库执行',
  };
  const statusNames = {
    recorded: '已记录', started: '已开始', success: '成功',
    waiting: '等待', blocked: '已拦截', error: '失败',
  };
  const reasonNames = {
    stale_revision: '提交的是旧版确认卡',
    stage_conflict: '当前阶段不再接受该操作',
    planner_failed: '未能生成有效计划',
    plan_rejected: '计划未通过规则校验',
    insufficient_evidence: '查询条件缺少明确依据',
    already_claimed: '该版本已取得执行权，已阻止重复查询',
    query_execution_failed: '执行器返回查询错误',
  };
  const sourceNames = { question: '自然语言提问', plan: '固定查询计划', checkpoint: '已保存的会话' };
  const summaryFields = {
    source: ['数据来源', '说明当前会话从哪里创建，或本次读取从哪里恢复。', sourceNames],
    metric: ['指标口径', '记录计划采用的业务指标代码，例如 paid 代表支付金额。'],
    dimension: ['分组维度', '记录查询如何分组，例如 day 代表按天。'],
    missing_fields: ['待补字段', '列出还需要用户提供或确认的查询条件。', fieldNames],
    row_count: ['返回行数', '成功执行后，由执行器返回的数据行数。'],
    submitted_revision: ['提交版本', '客户端提交操作时携带的确认卡版本，用于与 revision 核对。'],
    action: ['人工动作', '说明这次恢复是点选、补充说明、确认还是取消。', actionNames],
    executions: ['成功执行次数', '该会话已成功完成的查询次数。'],
  };

  function valueText(value) {
    if (value === undefined) return '未提供';
    if (value === null) return 'null';
    return typeof value === 'string' ? value : JSON.stringify(value);
  }

  function translated(value, names) {
    if (!names) return '';
    if (Array.isArray(value)) return value.map(item => names[item]).filter(Boolean).join('、');
    return names[value] || '';
  }

  function field(key, label, value, explanation, interpretation = '') {
    const item = element('div', 'trace-field');
    const title = element('dt', 'trace-field-label', label);
    title.append(element('code', 'trace-field-key', key));
    const content = element('dd', 'trace-field-content');
    content.append(element('code', 'trace-field-value', valueText(value)));
    if (interpretation) content.append(element('span', 'trace-field-interpretation', interpretation));
    content.append(element('span', 'trace-field-description', explanation));
    item.append(title, content);
    item.dataset.field = key;
    return item;
  }

  function eventDetails(event) {
    const content = element('div', 'trace-event-content');
    const metadata = element('dl', 'trace-fields');
    metadata.append(
      field('step', '步骤序号', event.step, '本会话内的事件顺序，用来还原流程及分页。'),
      field('timestamp', '记录时间', event.timestamp, '事件写入时的 UTC 时间。末尾 Z 表示 UTC，外层时间线按本地时间显示。'),
      field('name', '事件名称', event.name, '用来识别发生了哪一步，也可按此字段筛选事件。', titles[event.name]),
      field('category', '事件分类', event.category, '按会话、模型、校验、人工操作或查询等类别归档。', categoryNames[event.category]),
      field('stage', '会话阶段', event.stage, '事件发生时整个会话所处的阶段。', stageNames[event.stage]),
      field('status', '事件状态', event.status, '这一步自身是开始、成功、等待、拦截还是失败。', statusNames[event.status]),
      field('revision', '确认卡版本', event.revision, '记录当时的会话版本，用于核对用户操作对应的是哪张卡。'),
      field('duration_ms', '耗时', event.duration_ms, '本步骤的耗时，单位为毫秒。', event.duration_ms === null ? '这一事件没有计时' : ''),
      field('reason_code', '原因码', event.reason_code, '用固定代码说明追问、拦截或失败的原因。', event.reason_code === null ? '这一事件没有单独的原因码' : reasonNames[event.reason_code]),
      field('session_id', '会话 ID', event.session_id, '把同一次问数中的提问、补充和确认串在一起。'),
      field('request_id', '请求 ID', event.request_id, '把同一次接口操作产生的多个节点串在一起。'),
      field('event_id', '事件 ID', event.event_id, '唯一标识这条轨迹记录，方便定位具体事件。'),
      field('schema_version', '数据结构版本', event.schema_version, '标明这条记录采用的字段格式，便于后续兼容旧记录。'),
    );
    content.append(metadata);

    const summary = element('div', 'trace-summary-fields');
    summary.append(
      element('strong', 'trace-field-label', '事件摘要'),
      element('code', 'trace-field-key', 'summary'),
      element('p', 'trace-summary-description', '保存这一步特有的公开补充信息。'),
    );
    const fields = Object.entries(event.summary || {});
    if (!fields.length) {
      summary.append(element('code', 'trace-field-value', '{}'));
      summary.append(element('span', 'trace-field-interpretation', '这一事件没有额外摘要字段'));
    } else {
      const items = element('dl', 'trace-fields');
      for (const [key, value] of fields) {
        const [label, description, names] = summaryFields[key] || [key, '这一事件附带的公开摘要字段。'];
        items.append(field(key, label, value, description, translated(value, names)));
      }
      summary.append(items);
    }
    content.append(summary);
    return content;
  }

  let path = '';
  let cursor = 0;
  let active = null;
  let hasMore = false;

  function detail(event) {
    const summary = event.summary || {};
    switch (event.name) {
      case 'clarification_required': {
        const missing = (summary.missing_fields || []).map(key => fieldNames[key]).filter(Boolean);
        return missing.length ? `待补充：${missing.join('、')}` : '需要进一步说明查询条件';
      }
      case 'approval_required':
        return `确认卡版本 ${event.revision} · 等待批准执行`;
      case 'approval_accepted':
        return `已批准版本 ${summary.submitted_revision ?? event.revision} 的确认卡`;
      case 'revision_conflict':
        return event.reason_code === 'stale_revision'
          ? `提交版本 ${summary.submitted_revision}，当前版本 ${event.revision}`
          : '当前阶段已停止接受人工操作';
      case 'workflow_resumed':
        return actionNames[summary.action] || '';
      case 'sql_finished':
        return Number.isInteger(summary.row_count) ? `返回 ${summary.row_count} 行` : '';
      default:
        return '';
    }
  }

  function element(tag, className, text = '') {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  }

  function eventRow(event) {
    const status = statuses.has(event.status) ? event.status : 'recorded';
    const row = element('li', 'trace-event');
    row.dataset.status = status;
    row.dataset.eventName = event.name;
    row.dataset.eventId = event.event_id;
    const marker = element('span', 'trace-marker');
    marker.setAttribute('aria-hidden', 'true');

    const body = element('div', 'trace-event-body');
    const title = element('div', 'trace-event-heading');
    title.append(element('strong', 'trace-event-name', titles[event.name] || '流程更新'));
    if (statusLabels[status] && event.name !== 'approval_required' && event.name !== 'clarification_required') {
      title.append(element('span', 'trace-event-status', statusLabels[status]));
    }
    body.append(title);
    const subtitle = detail(event);
    if (subtitle) body.append(element('p', 'trace-event-detail', subtitle));

    const meta = element('div', 'trace-event-meta');
    const date = new Date(event.timestamp);
    if (Number.isFinite(date.getTime())) {
      const time = element('time', 'trace-event-time', timestamp.format(date));
      time.dateTime = event.timestamp;
      time.title = date.toLocaleString('zh-CN');
      meta.append(time);
    }
    if (Number.isFinite(event.duration_ms)) {
      const duration = event.duration_ms >= 1000
        ? `${(event.duration_ms / 1000).toFixed(1)} 秒`
        : `${event.duration_ms} 毫秒`;
      meta.append(element('span', 'trace-event-duration', duration));
    }
    const disclosure = element('details', 'trace-event-disclosure');
    const toggle = element('summary', 'trace-event-toggle');
    const chevron = element('span', 'trace-chevron trace-event-chevron');
    chevron.setAttribute('aria-hidden', 'true');
    toggle.append(marker, body, meta, chevron);
    disclosure.append(toggle, eventDetails(event));
    row.append(disclosure);
    return row;
  }

  function setMessage(text) {
    message.textContent = text;
    message.hidden = !text;
  }

  async function load(append = false) {
    if (!path || !panel.open) return;
    if (active) active.abort();
    const controller = new AbortController();
    active = controller;
    more.hidden = true;
    list.setAttribute('aria-busy', 'true');
    if (!list.children.length) setMessage('正在读取轨迹…');
    try {
      const response = await fetch(`${path}?after=${append ? cursor : 0}&limit=30`, {
        signal: controller.signal, cache: 'no-store',
      });
      if (!response.ok) throw new Error('Trace unavailable');
      const page = await response.json();
      if (active !== controller) return;

      const openIds = new Set(Array.from(list.children)
        .filter(row => row.firstElementChild.open)
        .map(row => row.dataset.eventId));
      const rows = page.events.map(event => {
        const row = eventRow(event);
        row.firstElementChild.open = openIds.has(event.event_id);
        return row;
      });
      if (append) list.append(...rows);
      else list.replaceChildren(...rows);
      cursor = page.next_after;
      hasMore = page.has_more;
      history.hidden = append ? history.hidden && !page.history_trimmed : !page.history_trimmed;
      setMessage(list.children.length ? '' : '这个会话暂无可用的执行记录。');
    } catch {
      if (active !== controller || controller.signal.aborted) return;
      setMessage('暂时无法读取轨迹，请点击刷新重试。');
    } finally {
      if (active === controller) {
        active = null;
        list.setAttribute('aria-busy', 'false');
        more.hidden = !hasMore;
      }
    }
  }

  function sync(session) {
    const nextPath = session ? `/sessions/${encodeURIComponent(session.id)}/trace` : '';
    if (nextPath !== path) {
      if (active) active.abort();
      active = null;
      path = nextPath;
      cursor = 0;
      hasMore = false;
      panel.open = false;
      list.replaceChildren();
      list.setAttribute('aria-busy', 'false');
      setMessage('');
      history.hidden = true;
      more.hidden = true;
      if (path) raw.href = path;
      else raw.removeAttribute('href');
    }
    panel.hidden = !session;
    if (session) stageLabel.textContent = stages[session.stage] || '处理中';
    if (panel.open) void load();
  }

  panel.addEventListener('toggle', event => {
    if (event.target !== panel) return;
    if (panel.open) void load();
    else if (active) {
      active.abort();
      active = null;
      list.setAttribute('aria-busy', 'false');
    }
  });
  refresh.addEventListener('click', () => void load());
  more.addEventListener('click', () => void load(true));
  return { sync };
})();
