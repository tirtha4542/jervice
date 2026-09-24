'use strict';

const STORAGE_PREFIX = 'tavonza.console.';
const VALID_ROLES = new Set(['manager', 'waiter', 'kitchen', 'cashier', 'customer']);
const STATUS_CLASSES = new Map([
  ['available', 'available'],
  ['reserved', 'reserved'],
  ['seated', 'seated'],
  ['dirty', 'dirty'],
  ['blocked', 'blocked'],
  ['open', 'open'],
  ['ordering', 'ordering'],
  ['dining', 'dining'],
  ['settling', 'settling'],
  ['closed', 'neutral'],
  ['joined', 'joined'],
  ['draft', 'draft'],
  ['submitted', 'submitted'],
  ['in_kitchen', 'in_kitchen'],
  ['ready', 'ready'],
  ['served', 'served'],
  ['cancelled', 'cancelled'],
  ['queued', 'queued'],
  ['prepping', 'prepping'],
  ['fired', 'fired'],
  ['picked_up', 'picked_up'],
  ['voided', 'voided'],
  ['unpaid', 'unpaid'],
  ['partial', 'partial'],
  ['authorized', 'authorized'],
  ['settled', 'settled'],
  ['failed', 'failed'],
  ['refunded', 'refunded'],
]);

const VIEW_META = Object.freeze({
  overview: { eyebrow: 'Live operations', title: 'Service overview' },
  tables: { eyebrow: 'Floor control', title: 'Tables & sessions' },
  orders: { eyebrow: 'Kitchen command', title: 'Active orders' },
  payments: { eyebrow: 'Settlement desk', title: 'Payments' },
  guests: { eyebrow: 'Guest experience', title: 'QR join test' },
});

const state = {
  token: storageGet('token') || '',
  branchId: positiveInteger(storageGet('branchId')) || 8,
  role: VALID_ROLES.has(storageGet('role')) ? storageGet('role') : 'manager',
  branchName: 'Branch 8',
  branchTimezone: '',
  view: 'overview',
  online: null,
  health: null,
  branches: [],
  dashboard: null,
  tables: [],
  sessions: [],
  orders: [],
  payments: [],
  paymentOrders: [],
  jarvisSessionId: createSessionId(),
  pendingSync: 0,
  tableQuery: '',
  orderQuery: '',
  orderStatus: 'all',
};

const byId = (id) => document.getElementById(id);
const ui = {
  body: document.body,
  appShell: byId('appShell'),
  sidebar: byId('sidebar'),
  sidebarScrim: byId('sidebarScrim'),
  menuButton: byId('menuButton'),
  sidebarClose: byId('sidebarClose'),
  viewTitle: byId('viewTitle'),
  viewEyebrow: byId('viewEyebrow'),
  branchSelect: byId('branchSelect'),
  roleSelect: byId('roleSelect'),
  refreshButton: byId('refreshButton'),
  openAccessButton: byId('openAccessButton'),
  accessAvatar: byId('accessAvatar'),
  accessModeLabel: byId('accessModeLabel'),
  sidebarBranchName: byId('sidebarBranchName'),
  sidebarStatusDot: byId('sidebarStatusDot'),
  connectionMiniDot: byId('connectionMiniDot'),
  connectionMiniText: byId('connectionMiniText'),
  connectionMiniMeta: byId('connectionMiniMeta'),
  healthStatus: byId('healthStatus'),
  healthDot: byId('healthDot'),
  healthLatency: byId('healthLatency'),
  moneyDue: byId('moneyDue'),
  moneyDueCount: byId('moneyDueCount'),
  lensStatus: byId('lensStatus'),
  tokenStatus: byId('tokenStatus'),
  lastSync: byId('lastSync'),
  heroDate: byId('heroDate'),
  heroTime: byId('heroTime'),
  heroTimezone: byId('heroTimezone'),
  metricGrid: byId('metricGrid'),
  servicePulse: byId('servicePulse'),
  activityList: byId('activityList'),
  auditCount: byId('auditCount'),
  chatLog: byId('chatLog'),
  chatForm: byId('chatForm'),
  chatInput: byId('chatInput'),
  chatSendButton: byId('chatSendButton'),
  newChatButton: byId('newChatButton'),
  tableSearch: byId('tableSearch'),
  refreshTablesButton: byId('refreshTablesButton'),
  tableGrid: byId('tableGrid'),
  tableCountLabel: byId('tableCountLabel'),
  sessionList: byId('sessionList'),
  openSessionCount: byId('openSessionCount'),
  navTableCount: byId('navTableCount'),
  orderSearch: byId('orderSearch'),
  orderStatusFilter: byId('orderStatusFilter'),
  refreshOrdersButton: byId('refreshOrdersButton'),
  orderSummary: byId('orderSummary'),
  orderList: byId('orderList'),
  navOrderCount: byId('navOrderCount'),
  paymentSessionSelect: byId('paymentSessionSelect'),
  refreshPaymentsButton: byId('refreshPaymentsButton'),
  paymentSummary: byId('paymentSummary'),
  paymentList: byId('paymentList'),
  paymentCountLabel: byId('paymentCountLabel'),
  paymentForm: byId('paymentForm'),
  paymentOrderSelect: byId('paymentOrderSelect'),
  paymentAmount: byId('paymentAmount'),
  paymentMethod: byId('paymentMethod'),
  paymentGuestId: byId('paymentGuestId'),
  postPaymentButton: byId('postPaymentButton'),
  paymentFormMessage: byId('paymentFormMessage'),
  guestSessionSelect: byId('guestSessionSelect'),
  guestJoinForm: byId('guestJoinForm'),
  guestName: byId('guestName'),
  guestPhone: byId('guestPhone'),
  guestOtp: byId('guestOtp'),
  guestQrToken: byId('guestQrToken'),
  guestTestConfirm: byId('guestTestConfirm'),
  guestJoinButton: byId('guestJoinButton'),
  guestJoinMessage: byId('guestJoinMessage'),
  accessDialog: byId('accessDialog'),
  accessForm: byId('accessForm'),
  closeAccessButton: byId('closeAccessButton'),
  tokenInput: byId('tokenInput'),
  bootstrapInput: byId('bootstrapInput'),
  toggleTokenButton: byId('toggleTokenButton'),
  accessBranchInput: byId('accessBranchInput'),
  accessRoleSelect: byId('accessRoleSelect'),
  sessionTokenCheck: byId('sessionTokenCheck'),
  accessMessage: byId('accessMessage'),
  accessSubmitButton: byId('accessSubmitButton'),
  devBootstrapButton: byId('devBootstrapButton'),
  clearTokenButton: byId('clearTokenButton'),
  toastRegion: byId('toastRegion'),
};

class ApiError extends Error {
  constructor(message, status = 0, payload = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

function storageGet(key) {
  try {
    return window.sessionStorage.getItem(`${STORAGE_PREFIX}${key}`);
  } catch {
    return null;
  }
}

function storageSet(key, value) {
  try {
    if (value === null || value === undefined) window.sessionStorage.removeItem(`${STORAGE_PREFIX}${key}`);
    else window.sessionStorage.setItem(`${STORAGE_PREFIX}${key}`, String(value));
  } catch {
    // The console remains usable when browser storage is unavailable.
  }
}

function positiveInteger(value) {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

function createSessionId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function replaceChildren(parent, children) {
  parent.replaceChildren(...(Array.isArray(children) ? children : [children]));
  return parent;
}

function setButtonBusy(button, busy, busyText = 'Working…') {
  if (!button) return;
  if (busy) {
    button.dataset.previousText = button.textContent;
    button.textContent = busyText;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
  } else {
    button.textContent = button.dataset.previousText || button.textContent;
    button.disabled = false;
    button.removeAttribute('aria-busy');
    delete button.dataset.previousText;
  }
}

function normalizeToken(value) {
  const trimmed = String(value || '').trim();
  if (!trimmed) return '';
  return /^bearer\s+/i.test(trimmed) ? trimmed.replace(/^bearer\s+/i, '') : trimmed;
}

function normalizeStatus(value) {
  const normalized = String(value ?? 'unknown').trim().toLowerCase().replace(/[\s-]+/g, '_');
  return STATUS_CLASSES.get(normalized) || 'neutral';
}

function humanize(value) {
  const text = String(value ?? '').trim().replace(/[_-]+/g, ' ');
  if (!text) return 'Unknown';
  return text.replace(/\b\w/g, (character) => character.toUpperCase());
}

function statusBadge(value) {
  return createElement('span', `badge badge--${normalizeStatus(value)}`, humanize(value));
}

function priorityClass(value) {
  const priority = String(value || 'medium').toLowerCase();
  return new Set(['low', 'medium', 'high', 'critical']).has(priority) ? priority : 'medium';
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function numberValue(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function formatMoney(value) {
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
  }).format(numberValue(value));
}

function formatDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function formatShortTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(date);
}

function pluralize(count, singular, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}

function showToast(message, tone = 'info') {
  const allowedTones = new Set(['info', 'success', 'error', 'warning']);
  const toast = createElement('div', `toast toast--${allowedTones.has(tone) ? tone : 'info'}`, message);
  ui.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 4400);
}

function setFormMessage(element, message = '', tone = '') {
  element.textContent = message;
  element.classList.remove('is-success', 'is-error');
  if (tone) element.classList.add(`is-${tone}`);
}

function emptyState(title, message, icon = '◇') {
  const wrapper = createElement('div', 'empty-state');
  wrapper.append(
    createElement('span', 'empty-state__icon', icon),
    createElement('strong', '', title),
    createElement('p', '', message),
  );
  return wrapper;
}

function errorState(error, retry) {
  const wrapper = createElement('div', 'error-state');
  wrapper.append(
    createElement('span', 'error-state__icon', '!'),
    createElement('strong', '', 'Could not load this view'),
    createElement('p', '', error instanceof Error ? error.message : String(error)),
  );
  if (typeof retry === 'function') {
    const button = createElement('button', 'secondary-button', 'Try again');
    button.type = 'button';
    button.addEventListener('click', retry);
    wrapper.append(button);
  }
  return wrapper;
}

function loadingBlock(label = 'Loading…') {
  return createElement('div', 'loading-block', label);
}

function loadingLine(label = 'Loading…') {
  return createElement('div', 'loading-line', label);
}

function startSync() {
  state.pendingSync += 1;
  ui.body.classList.add('is-syncing');
}

function endSync() {
  state.pendingSync = Math.max(0, state.pendingSync - 1);
  if (state.pendingSync === 0) ui.body.classList.remove('is-syncing');
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), numberValue(options.timeout, 20000));
  const headers = new Headers(options.headers || {});
  headers.set('Accept', 'application/json');

  if (options.auth !== false && state.token) {
    headers.set('Authorization', `Bearer ${state.token}`);
  }
  if (options.body !== undefined && options.body !== null && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  let response;
  try {
    response = await fetch(path, {
      method: options.method || 'GET',
      headers,
      body: options.body === undefined || options.body === null
        ? undefined
        : (typeof options.body === 'string' ? options.body : JSON.stringify(options.body)),
      cache: 'no-store',
      credentials: 'same-origin',
      signal: controller.signal,
    });
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new ApiError('The backend request timed out. Check the service and try again.', 0);
    }
    throw new ApiError('The console cannot reach the backend through the local proxy.', 0);
  } finally {
    window.clearTimeout(timeout);
  }

  const contentType = response.headers.get('content-type') || '';
  let payload = null;
  if (response.status !== 204) {
    if (contentType.includes('application/json')) {
      payload = await response.json().catch(() => null);
    } else {
      payload = await response.text().catch(() => '');
    }
  }

  if (!response.ok) {
    throw new ApiError(apiErrorMessage(payload, response), response.status, payload);
  }
  return payload;
}

function apiErrorMessage(payload, response) {
  if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
    if (typeof payload.detail === 'string' && payload.detail) return payload.detail;
    if (typeof payload.error === 'string' && payload.error) return payload.error;
    if (typeof payload.message === 'string' && payload.message) return payload.message;
  }
  if (Array.isArray(payload)) {
    const messages = payload
      .map((entry) => (entry && typeof entry === 'object' ? entry.msg || entry.message : null))
      .filter(Boolean);
    if (messages.length) return messages.join(' · ');
  }
  if (typeof payload === 'string' && payload.trim()) return payload.trim().slice(0, 500);
  return `Backend request failed with HTTP ${response.status}.`;
}

async function checkHealth() {
  const started = performance.now();
  try {
    const health = await api('/health', { auth: false, timeout: 8000 });
    const latency = Math.max(1, Math.round(performance.now() - started));
    state.online = true;
    state.health = health;
    updateConnectionUi(true, health, latency);
    return health;
  } catch (error) {
    state.online = false;
    state.health = null;
    updateConnectionUi(false, null, 0, error);
    throw error;
  }
}

function updateConnectionUi(online, health, latency, error = null) {
  const statusText = online ? humanize(health && health.status) : 'Unavailable';
  const serviceText = online && health && health.service ? String(health.service) : 'Backend proxy';
  ui.healthStatus.textContent = statusText;
  ui.healthLatency.textContent = online ? `${latency} ms` : '—';
  ui.connectionMiniText.textContent = online ? `${serviceText} online` : 'Backend unavailable';
  ui.connectionMiniMeta.textContent = online ? `${latency} ms via secure proxy` : (error?.message || 'Connection error');

  for (const dot of [ui.healthDot, ui.sidebarStatusDot, ui.connectionMiniDot]) {
    dot.className = `status-dot ${online ? 'status-dot--ok' : 'status-dot--error'}`;
    dot.title = online ? 'Backend connected' : 'Backend disconnected';
  }
}

async function loadBranches() {
  const branches = asArray(await api('/api/v1/org/branches', { timeout: 12000 }));
  state.branches = branches.filter((branch) => branch && positiveInteger(branch.id));
  populateBranchSelect();
  updateContextUi();
}

function populateBranchSelect() {
  const selected = state.branchId;
  const options = state.branches.length
    ? state.branches.map((branch) => {
      const option = document.createElement('option');
      option.value = String(branch.id);
      option.textContent = `${branch.name || `Branch ${branch.id}`} · ${branch.id}`;
      return option;
    })
    : [Object.assign(document.createElement('option'), { value: String(selected), textContent: `Branch ${selected}` })];

  replaceChildren(ui.branchSelect, options);
  if (options.some((option) => Number(option.value) === selected)) {
    ui.branchSelect.value = String(selected);
  } else if (options[0]) {
    state.branchId = positiveInteger(options[0].value) || selected;
    ui.branchSelect.value = String(state.branchId);
  }
  storageSet('branchId', state.branchId);
}

function updateContextUi() {
  const selectedBranch = state.branches.find((branch) => Number(branch.id) === state.branchId);
  state.branchName = selectedBranch?.name || `Branch ${state.branchId}`;
  state.branchTimezone = selectedBranch?.timezone || '';
  ui.branchSelect.value = String(state.branchId);
  ui.roleSelect.value = state.role;
  ui.accessRoleSelect.value = state.role;
  ui.accessBranchInput.value = String(state.branchId);
  ui.sidebarBranchName.textContent = state.branchName;
  ui.lensStatus.textContent = `${humanize(state.role)} lens`;
  ui.accessAvatar.textContent = state.role.slice(0, 1);
  ui.accessModeLabel.textContent = state.token ? 'Bearer session' : 'Dev bootstrap';
  ui.tokenStatus.textContent = state.token ? 'JWT' : 'DEV';
  updateClock();
}

function updateClock() {
  const now = new Date();
  let dateFormatter;
  let timeFormatter;
  try {
    const options = state.branchTimezone ? { timeZone: state.branchTimezone } : {};
    dateFormatter = new Intl.DateTimeFormat(undefined, { ...options, weekday: 'long', month: 'short', day: 'numeric' });
    timeFormatter = new Intl.DateTimeFormat(undefined, { ...options, hour: '2-digit', minute: '2-digit' });
  } catch {
    dateFormatter = new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
    timeFormatter = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' });
  }
  ui.heroDate.textContent = dateFormatter.format(now);
  ui.heroTime.textContent = timeFormatter.format(now);
  ui.heroTimezone.textContent = state.branchTimezone ? `${state.branchTimezone} · branch time` : 'Branch local time';
}

function showView(view) {
  if (!VIEW_META[view]) return;
  state.view = view;
  const meta = VIEW_META[view];
  ui.viewTitle.textContent = meta.title;
  ui.viewEyebrow.textContent = meta.eyebrow;

  document.querySelectorAll('[data-view-panel]').forEach((panel) => {
    const active = panel.dataset.viewPanel === view;
    panel.hidden = !active;
    panel.classList.toggle('is-active', active);
    panel.classList.remove('is-entering');
    if (active) {
      void panel.offsetWidth;
      panel.classList.add('is-entering');
    }
  });

  document.querySelectorAll('[data-view-target]').forEach((button) => {
    const active = button.dataset.viewTarget === view;
    button.classList.toggle('is-active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });

  if (window.location.hash !== `#${view}`) {
    window.history.replaceState(null, '', `#${view}`);
  }
  closeMobileNav();
}

function openMobileNav() {
  ui.body.classList.add('nav-open');
  ui.menuButton.setAttribute('aria-expanded', 'true');
}

function closeMobileNav() {
  ui.body.classList.remove('nav-open');
  ui.menuButton.setAttribute('aria-expanded', 'false');
}

function viewContainers() {
  return {
    overview: [ui.metricGrid, ui.servicePulse, ui.activityList],
    tables: [ui.tableGrid, ui.sessionList],
    orders: [ui.orderSummary, ui.orderList],
    payments: [ui.paymentSummary, ui.paymentList],
    guests: [ui.guestSessionSelect],
  }[state.view] || [];
}

function renderCurrentError(error) {
  const retry = () => refreshCurrentView();
  for (const container of viewContainers()) {
    if (container === ui.metricGrid) replaceChildren(container, skeletonCards());
    else if (container.tagName === 'SELECT') {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = 'Unable to load';
      replaceChildren(container, option);
      container.disabled = true;
    } else replaceChildren(container, errorState(error, retry));
  }
}

async function refreshCurrentView({ notify = true } = {}) {
  startSync();
  try {
    if (state.view === 'overview') await loadDashboard();
    if (state.view === 'tables') await loadTables();
    if (state.view === 'orders') await loadOrders();
    if (state.view === 'payments') await loadPaymentView();
    if (state.view === 'guests') await loadGuestSessions();
  } catch (error) {
    renderCurrentError(error);
    if (notify) showToast(error.message, 'error');
  } finally {
    endSync();
  }
}

function skeletonCards() {
  return Array.from({ length: 4 }, () => {
    const card = createElement('article', 'metric-card skeleton-card');
    card.append(createElement('span'), createElement('span'), createElement('span'));
    return card;
  });
}

async function loadDashboard() {
  const branchId = state.branchId;
  replaceChildren(ui.metricGrid, skeletonCards());
  replaceChildren(ui.servicePulse, loadingLine('Loading branch context…'));
  replaceChildren(ui.activityList, loadingLine('Reading recent events…'));
  const dashboard = await api(`/api/v1/dashboard/${encodeURIComponent(branchId)}`, { timeout: 20000 });
  if (state.branchId !== branchId) return;
  state.dashboard = dashboard && typeof dashboard === 'object' ? dashboard : {};
  renderDashboard(state.dashboard);
}

function renderDashboard(dashboard) {
  const floor = dashboard.floor || {};
  const kitchen = dashboard.kitchen || {};
  const money = dashboard.money || {};
  const inventory = dashboard.inventory || {};
  const tablesByStatus = floor.tables_by_status || {};
  const seated = numberValue(tablesByStatus.seated);
  const available = numberValue(tablesByStatus.available);
  const tableTotal = numberValue(floor.tables_total);
  const activeGuests = numberValue(floor.active_guests);
  const openTickets = numberValue(kitchen.open_tickets);
  const outstanding = numberValue(money.outstanding_total);
  const outstandingCount = numberValue(money.outstanding_count);

  ui.moneyDue.textContent = formatMoney(outstanding);
  ui.moneyDueCount.textContent = `${outstandingCount} open`;
  ui.navTableCount.textContent = String(tableTotal);
  ui.lastSync.textContent = `Synced ${formatShortTime(new Date())}`;

  const metrics = [
    {
      label: 'Tables in service',
      value: seated,
      unit: `/ ${tableTotal}`,
      detail: `${available} available · ${numberValue(floor.open_sessions)} open sessions`,
      icon: '▦',
      className: 'metric-card--cyan',
    },
    {
      label: 'Active guests',
      value: activeGuests,
      unit: 'live',
      detail: `${numberValue(floor.open_sessions)} shared table visits`,
      icon: '⌁',
      className: 'metric-card--green',
    },
    {
      label: 'Kitchen load',
      value: openTickets,
      unit: 'tickets',
      detail: `${numberValue(kitchen.orders_by_status?.in_kitchen)} currently in kitchen`,
      icon: '≡',
      className: 'metric-card--gold',
    },
    {
      label: 'Outstanding',
      value: formatMoney(outstanding),
      unit: '',
      detail: `${formatMoney(money.settled_total)} settled in live ledger`,
      icon: '◇',
      className: 'metric-card--violet',
    },
  ];

  replaceChildren(ui.metricGrid, metrics.map((metric) => {
    const card = createElement('article', `metric-card ${metric.className}`);
    const top = createElement('div', 'metric-card__top');
    top.append(createElement('span', 'metric-card__label', metric.label), createElement('span', 'metric-card__icon', metric.icon));
    const value = createElement('div', 'metric-card__value');
    value.append(createElement('span', '', metric.value));
    if (metric.unit) value.append(createElement('small', '', metric.unit));
    card.append(top, value, createElement('span', 'metric-card__detail', metric.detail));
    return card;
  }));

  renderServicePulse(dashboard);
  renderActivity(dashboard.audit);
}

function renderServicePulse(dashboard) {
  const floor = dashboard.floor || {};
  const kitchen = dashboard.kitchen || {};
  const inventory = dashboard.inventory || {};
  const queues = kitchen.queues_by_station || {};
  const queueText = Object.entries(queues).map(([station, count]) => `${humanize(station)} ${count}`).join(' · ') || 'No active station queues';
  const rows = [
    ['Open table sessions', numberValue(floor.open_sessions), `${pluralize(numberValue(floor.sessions_by_status?.closed), 'closed visit', 'closed visits')} in look-back`],
    ['Kitchen tickets', numberValue(kitchen.open_tickets), queueText],
    ['Upcoming reservations', numberValue(dashboard.reservations?.by_status?.requested) + numberValue(dashboard.reservations?.by_status?.confirmed), 'Requested and confirmed'],
    ['Low-stock SKUs', numberValue(inventory.low_stock_skus), `${asArray(inventory.alerts).length} recipe impact alerts`],
  ];
  replaceChildren(ui.servicePulse, rows.map(([label, value, detail]) => {
    const row = createElement('div', 'pulse-row');
    row.append(createElement('span', '', label), createElement('strong', '', value), createElement('small', '', detail));
    return row;
  }));
}

function renderActivity(activity) {
  const entries = asArray(activity);
  ui.auditCount.textContent = pluralize(entries.length, 'event');
  if (!entries.length) {
    replaceChildren(ui.activityList, emptyState('No recent activity', 'Audit events from the selected look-back window will appear here.', '⌁'));
    return;
  }
  replaceChildren(ui.activityList, entries.slice(0, 8).map((entry) => {
    const row = createElement('div', 'activity-row');
    const marker = createElement('span', 'activity-row__marker');
    const copy = createElement('div');
    copy.append(
      createElement('strong', '', humanize(entry.event_type || 'System event')),
      createElement('span', '', `Actor: ${humanize(entry.actor_role || 'system')}`),
      createElement('time', '', formatDateTime(entry.created_at)),
    );
    row.append(marker, copy);
    return row;
  }));
}

async function loadTables() {
  const branchId = state.branchId;
  replaceChildren(ui.tableGrid, loadingBlock('Loading floor state…'));
  replaceChildren(ui.sessionList, loadingLine('Loading service sessions…'));
  const [tables, sessions] = await Promise.all([
    api(`/api/v1/tables?branch_id=${encodeURIComponent(branchId)}`, { timeout: 15000 }),
    api(`/api/v1/table-sessions?branch_id=${encodeURIComponent(branchId)}`, { timeout: 15000 }),
  ]);
  if (state.branchId !== branchId) return;
  state.tables = asArray(tables);
  state.sessions = asArray(sessions);
  ui.navTableCount.textContent = String(state.tables.length);
  renderTables();
  renderSessions();
}

function renderTables() {
  const query = state.tableQuery.trim().toLowerCase();
  const filtered = state.tables.filter((table) => {
    const haystack = `${table.code || ''} ${table.status || ''} ${table.id || ''}`.toLowerCase();
    return !query || haystack.includes(query);
  });
  ui.tableCountLabel.textContent = pluralize(state.tables.length, 'table');
  if (!filtered.length) {
    replaceChildren(ui.tableGrid, emptyState(
      query ? 'No matching tables' : 'No tables configured',
      query ? 'Try a different table code or status.' : 'Seed the backend or add a table to populate this branch.',
      '▦',
    ));
    return;
  }

  replaceChildren(ui.tableGrid, filtered.map((table) => {
    const card = createElement('article', 'table-tile');
    const top = createElement('div', 'table-tile__top');
    top.append(createElement('strong', 'table-code', table.code || `T${table.id}`), statusBadge(table.status));
    const meta = createElement('div', 'table-tile__meta');
    meta.append(
      createElement('span', '', `Seats ${numberValue(table.capacity)}`),
      createElement('span', '', table.active_session_id ? `Session #${table.active_session_id}` : 'No active visit'),
    );
    card.append(top, meta);
    if (!table.active_session_id && normalizeStatus(table.status) === 'available') {
      const action = createElement('div', 'table-tile__action');
      const button = createElement('button', 'secondary-button', 'Open session');
      button.type = 'button';
      button.dataset.tableId = String(table.id);
      button.dataset.tableCode = String(table.code || table.id);
      action.append(button);
      card.append(action);
    }
    return card;
  }));
}

function renderSessions() {
  const sessions = [...state.sessions].sort((left, right) => {
    const leftOpen = left.status === 'closed' ? 1 : 0;
    const rightOpen = right.status === 'closed' ? 1 : 0;
    return leftOpen - rightOpen || numberValue(right.id) - numberValue(left.id);
  });
  const open = sessions.filter((session) => session.status !== 'closed');
  ui.openSessionCount.textContent = `${open.length} open`;
  if (!sessions.length) {
    replaceChildren(ui.sessionList, emptyState('No service sessions', 'Open a table session to enable guest QR joins.', '◌'));
    return;
  }
  const tableMap = new Map(state.tables.map((table) => [numberValue(table.id), table]));
  replaceChildren(ui.sessionList, sessions.map((session) => {
    const card = createElement('article', 'session-card');
    const top = createElement('div', 'session-card__top');
    const table = tableMap.get(numberValue(session.table_id));
    top.append(createElement('strong', '', `${table?.code || 'Table'} · #${session.id}`), statusBadge(session.status));
    const meta = createElement('div', 'session-card__meta');
    meta.append(
      createElement('span', '', `Opened ${formatShortTime(session.opened_at)}`),
      createElement('span', '', session.closed_at ? `Closed ${formatShortTime(session.closed_at)}` : 'Live service'),
    );
    card.append(top, meta);
    return card;
  }));
}

async function handleOpenSession(button) {
  const tableId = positiveInteger(button.dataset.tableId);
  const tableCode = button.dataset.tableCode || `table ${tableId}`;
  if (!tableId) return;
  const confirmed = window.confirm(`Open a service session for ${tableCode}?`);
  if (!confirmed) return;
  setButtonBusy(button, true, 'Opening…');
  try {
    const session = await api(`/api/v1/tables/${tableId}/open-session`, { method: 'POST', body: {}, timeout: 15000 });
    showToast(`Session #${session?.id || 'created'} opened for ${tableCode}.`, 'success');
    await loadTables();
  } catch (error) {
    showToast(error.message, 'error');
    setButtonBusy(button, false);
  }
}

async function loadOrders() {
  const branchId = state.branchId;
  replaceChildren(ui.orderSummary, loadingBlock('Summarizing tickets…'));
  replaceChildren(ui.orderList, loadingBlock('Loading active orders…'));
  const orders = asArray(await api(`/api/v1/orders?branch_id=${encodeURIComponent(branchId)}`, { timeout: 20000 }));
  if (state.branchId !== branchId) return;
  state.orders = orders;
  ui.navOrderCount.textContent = String(orders.length);
  populateOrderStatusFilter();
  renderOrderSummary();
  renderOrders();
}

function populateOrderStatusFilter() {
  const selected = state.orderStatus;
  const statuses = [...new Set(state.orders.map((order) => normalizeStatus(order.status)))];
  const options = [new Option('All statuses', 'all')];
  statuses.forEach((status) => options.push(new Option(humanize(status), status)));
  replaceChildren(ui.orderStatusFilter, options);
  state.orderStatus = statuses.includes(selected) ? selected : 'all';
  ui.orderStatusFilter.value = state.orderStatus;
}

function renderOrderSummary() {
  const total = state.orders.reduce((sum, order) => sum + numberValue(order.total), 0);
  const kitchen = state.orders.filter((order) => ['submitted', 'in_kitchen'].includes(normalizeStatus(order.status))).length;
  const ready = state.orders.filter((order) => ['ready', 'served'].includes(normalizeStatus(order.status))).length;
  const items = state.orders.reduce((sum, order) => sum + asArray(order.items).length, 0);
  const summaries = [
    ['Active tickets', state.orders.length],
    ['Ticket value', formatMoney(total)],
    ['In kitchen', kitchen],
    ['Ready / served', ready],
    ['Line items', items],
  ];
  replaceChildren(ui.orderSummary, summaries.slice(0, 4).map(([label, value]) => {
    const item = createElement('div', 'summary-item');
    item.append(createElement('small', '', label), createElement('strong', '', value));
    return item;
  }));
}

function renderOrders() {
  const query = state.orderQuery.trim().toLowerCase();
  const filtered = state.orders.filter((order) => {
    if (state.orderStatus !== 'all' && normalizeStatus(order.status) !== state.orderStatus) return false;
    if (!query) return true;
    const items = asArray(order.items).map((item) => `${item.menu_item_name || ''} ${asArray(item.modifiers).join(' ')}`).join(' ');
    return `${order.id || ''} ${order.table_session_id || ''} ${items}`.toLowerCase().includes(query);
  });

  if (!filtered.length) {
    replaceChildren(ui.orderList, emptyState(
      state.orders.length ? 'No matching orders' : 'No active orders',
      state.orders.length ? 'Adjust the search or status filter.' : 'New or active service tickets for this branch will appear here.',
      '≡',
    ));
    return;
  }

  replaceChildren(ui.orderList, filtered.map((order) => {
    const card = createElement('article', 'order-card');
    const header = createElement('div', 'order-card__header');
    const identity = createElement('div', 'order-card__id');
    const copy = createElement('div');
    copy.append(
      createElement('strong', '', `Table session #${order.table_session_id}`),
      createElement('small', '', order.is_shared ? 'Shared order' : (order.guest_session_id ? `Guest #${order.guest_session_id}` : 'Individual order')),
    );
    identity.replaceChildren(createElement('span', 'order-number', `#${order.id}`), copy, statusBadge(order.status));
    header.append(identity);

    const meta = createElement('div', 'order-card__meta');
    meta.append(
      createElement('span', '', `${pluralize(asArray(order.items).length, 'item')}`),
      createElement('span', '', order.guest_session_id ? `Guest session #${order.guest_session_id}` : 'Table-wide'),
      createElement('span', '', order.is_shared ? 'Split-friendly' : 'Direct order'),
    );

    const items = createElement('div', 'order-items');
    const orderItems = asArray(order.items);
    if (!orderItems.length) {
      items.append(createElement('div', 'loading-line', 'No line items on this ticket.'));
    } else {
      orderItems.forEach((item) => {
        const row = createElement('div', 'order-item');
        const name = createElement('div', 'order-item__name');
        name.append(
          createElement('strong', '', item.menu_item_name || `Menu item #${item.menu_item_id}`),
          createElement('small', '', `${humanize(item.station)} · ${asArray(item.modifiers).join(', ') || 'No modifiers'}`),
        );
        const side = createElement('div', 'order-item__side');
        side.append(createElement('span', 'order-item__quantity', `${numberValue(item.quantity, 1)}×`), statusBadge(item.status));
        row.append(name, side);
        items.append(row);
      });
    }

    const footer = createElement('div', 'order-card__footer');
    footer.append(createElement('span', '', 'Ticket total'), createElement('strong', '', formatMoney(order.total)));
    card.append(header, meta, items, footer);
    return card;
  }));
}

async function fetchSessions() {
  const branchId = state.branchId;
  const sessions = asArray(await api(`/api/v1/table-sessions?branch_id=${encodeURIComponent(branchId)}`, { timeout: 15000 }));
  if (state.branchId !== branchId) return state.sessions;
  state.sessions = sessions;
  return sessions;
}

function sessionOption(session) {
  const table = state.tables.find((entry) => numberValue(entry.id) === numberValue(session.table_id));
  const option = document.createElement('option');
  option.value = String(session.id);
  option.textContent = `${table?.code || 'Table'} · Session #${session.id} · ${humanize(session.status)}`;
  return option;
}

function populateSessionSelect(select, sessions, { includePlaceholder = true, openOnly = false, preferred = '' } = {}) {
  const eligible = openOnly ? sessions.filter((session) => session.status !== 'closed') : sessions;
  const children = [];
  if (includePlaceholder) children.push(new Option('Choose a session', ''));
  eligible.forEach((session) => children.push(sessionOption(session)));
  replaceChildren(select, children);
  select.disabled = eligible.length === 0;
  const preferredId = positiveInteger(preferred);
  if (preferredId && eligible.some((session) => numberValue(session.id) === preferredId)) {
    select.value = String(preferredId);
  } else if (eligible.length && !includePlaceholder) {
    select.value = String(eligible[0].id);
  }
}

async function loadPaymentView() {
  ui.paymentSummary.replaceChildren(loadingBlock('Loading selected session…'));
  replaceChildren(ui.paymentList, loadingBlock('Loading payment activity…'));
  const previous = ui.paymentSessionSelect.value;
  const sessions = await fetchSessions();
  populateSessionSelect(ui.paymentSessionSelect, sessions, { preferred: previous || state.sessions.find((session) => session.status !== 'closed')?.id || '' });
  if (!ui.paymentSessionSelect.value) {
    setPaymentEmpty('No table sessions are available for this branch.');
    return;
  }
  await loadPaymentSession();
}

function setPaymentEmpty(message) {
  const empty = emptyState('Nothing to settle yet', message, '◇');
  replaceChildren(ui.paymentSummary, summaryItems([['Session', '—'], ['Payments', '0'], ['Settled', formatMoney(0)], ['Outstanding', formatMoney(0)]]));
  replaceChildren(ui.paymentList, empty);
  ui.paymentCountLabel.textContent = '0 records';
  replaceChildren(ui.paymentOrderSelect, [new Option('No active orders', '')]);
  ui.paymentOrderSelect.disabled = true;
  ui.postPaymentButton.disabled = true;
}

function summaryItems(rows) {
  return rows.map(([label, value]) => {
    const item = createElement('div', 'summary-item');
    item.append(createElement('small', '', label), createElement('strong', '', value));
    return item;
  });
}

async function loadPaymentSession() {
  const sessionId = positiveInteger(ui.paymentSessionSelect.value);
  if (!sessionId) {
    setPaymentEmpty('Choose a table session to begin.');
    return;
  }
  replaceChildren(ui.paymentList, loadingBlock('Loading payment activity…'));
  const [payments, orders] = await Promise.all([
    api(`/api/v1/payments?table_session_id=${sessionId}`, { timeout: 15000 }),
    api(`/api/v1/orders?branch_id=${encodeURIComponent(state.branchId)}&table_session_id=${sessionId}`, { timeout: 15000 }),
  ]);
  if (positiveInteger(ui.paymentSessionSelect.value) !== sessionId) return;
  state.payments = asArray(payments);
  state.paymentOrders = asArray(orders);
  renderPayments();
}

function paymentOrderTotal(orderId = null) {
  if (orderId !== null) {
    return numberValue(state.paymentOrders.find((order) => String(order.id) === String(orderId))?.total);
  }
  return state.paymentOrders.reduce((sum, order) => sum + numberValue(order.total), 0);
}

function recordedPaymentAmount(orderId = null) {
  return state.payments
    .filter((payment) => orderId === null || String(payment.order_id) === String(orderId))
    .filter((payment) => !['failed', 'refunded'].includes(normalizeStatus(payment.status)))
    .reduce((sum, payment) => sum + numberValue(payment.amount), 0);
}

function remainingForOrder(orderId) {
  return Math.max(0, paymentOrderTotal(orderId) - recordedPaymentAmount(orderId));
}

function renderPayments() {
  const total = paymentOrderTotal();
  const settled = state.payments
    .filter((payment) => normalizeStatus(payment.status) === 'settled')
    .reduce((sum, payment) => sum + numberValue(payment.amount), 0);
  const outstanding = Math.max(0, total - recordedPaymentAmount());
  replaceChildren(ui.paymentSummary, summaryItems([
    ['Session', `#${ui.paymentSessionSelect.value}`],
    ['Payments', state.payments.length],
    ['Settled', formatMoney(settled)],
    ['Outstanding', formatMoney(outstanding)],
  ]));
  ui.paymentCountLabel.textContent = pluralize(state.payments.length, 'record');
  renderPaymentRecords();
  populatePaymentOrders();
}

function renderPaymentRecords() {
  if (!state.payments.length) {
    replaceChildren(ui.paymentList, emptyState('No payments posted', 'Use the form to create a new payment intent for this table session.', '◇'));
    return;
  }
  const methodIcons = new Map([['card', '▤'], ['cash', '$'], ['split', '⑂'], ['wallet', '◇']]);
  replaceChildren(ui.paymentList, state.payments.map((payment) => {
    const row = createElement('article', 'payment-record');
    const main = createElement('div', 'payment-record__main');
    const copy = createElement('div', 'payment-record__copy');
    copy.append(
      createElement('strong', '', `Payment #${payment.id} · Order #${payment.order_id}`),
      createElement('small', '', `${humanize(payment.method)}${payment.guest_session_id ? ` · Guest #${payment.guest_session_id}` : ' · Shared'}`),
    );
    main.replaceChildren(createElement('span', 'payment-record__icon', methodIcons.get(String(payment.method || '').toLowerCase()) || '◇'), copy);

    const side = createElement('div', 'payment-record__side');
    side.append(createElement('strong', '', formatMoney(payment.amount)), statusBadge(payment.status));
    if (['unpaid', 'partial', 'authorized'].includes(normalizeStatus(payment.status))) {
      const settle = createElement('button', 'secondary-button', 'Settle');
      settle.type = 'button';
      settle.dataset.paymentId = String(payment.id);
      settle.dataset.paymentStatus = normalizeStatus(payment.status);
      settle.dataset.paymentAmount = formatMoney(payment.amount);
      side.append(settle);
    }
    row.append(main, side);
    return row;
  }));
}

function populatePaymentOrders() {
  const selected = ui.paymentOrderSelect.value;
  const options = [new Option(state.paymentOrders.length ? 'Select an order' : 'No active orders', '')];
  state.paymentOrders.forEach((order) => {
    options.push(new Option(`Order #${order.id} · ${formatMoney(order.total)}`, String(order.id)));
  });
  replaceChildren(ui.paymentOrderSelect, options);
  const selectedOrder = state.paymentOrders.find((order) => String(order.id) === selected);
  if (selectedOrder) ui.paymentOrderSelect.value = selected;
  const hasOrders = state.paymentOrders.length > 0;
  const remaining = selectedOrder ? remainingForOrder(selectedOrder.id) : 0;
  ui.paymentOrderSelect.disabled = !hasOrders;
  ui.postPaymentButton.disabled = !selectedOrder || remaining <= 0;
  if (selectedOrder && remaining > 0) ui.paymentAmount.value = remaining.toFixed(2);
  else ui.paymentAmount.value = '';
  setFormMessage(ui.paymentFormMessage, selectedOrder && remaining <= 0
    ? 'This order is already covered by a recorded payment. Use Settle on the payment below.'
    : '');
}

async function handlePaymentSubmit(event) {
  event.preventDefault();
  const orderId = positiveInteger(ui.paymentOrderSelect.value);
  const amount = Number(ui.paymentAmount.value);
  const guestId = positiveInteger(ui.paymentGuestId.value);
  if (!orderId || !Number.isFinite(amount) || amount <= 0) {
    setFormMessage(ui.paymentFormMessage, 'Choose an order and enter an amount greater than zero.', 'error');
    return;
  }
  setButtonBusy(ui.postPaymentButton, true, 'Posting…');
  setFormMessage(ui.paymentFormMessage, 'Creating payment intent…');
  try {
    const payment = await api('/api/v1/payments', {
      method: 'POST',
      body: {
        order_id: orderId,
        guest_session_id: guestId,
        amount: amount.toFixed(2),
        method: ui.paymentMethod.value,
      },
      timeout: 15000,
    });
    setFormMessage(ui.paymentFormMessage, `Payment #${payment?.id || ''} posted.`, 'success');
    showToast('Payment intent created.', 'success');
    await loadPaymentSession();
  } catch (error) {
    setFormMessage(ui.paymentFormMessage, error.message, 'error');
  } finally {
    setButtonBusy(ui.postPaymentButton, false);
  }
}

async function handleSettlePayment(button) {
  const paymentId = positiveInteger(button.dataset.paymentId);
  if (!paymentId) return;
  if (!window.confirm(`Settle payment #${paymentId} for ${button.dataset.paymentAmount || 'the recorded amount'}?`)) return;
  setButtonBusy(button, true, 'Settling…');
  try {
    const currentStatus = button.dataset.paymentStatus || 'unpaid';
    if (['unpaid', 'partial'].includes(currentStatus)) {
      await api(`/api/v1/payments/${paymentId}/status`, {
        method: 'PATCH',
        body: { status: 'authorized' },
        timeout: 15000,
      });
    }
    await api(`/api/v1/payments/${paymentId}/status`, {
      method: 'PATCH',
      body: { status: 'settled' },
      timeout: 15000,
    });
    showToast(`Payment #${paymentId} settled.`, 'success');
    await loadPaymentSession();
  } catch (error) {
    showToast(error.message, 'error');
    setButtonBusy(button, false);
  }
}

async function loadGuestSessions() {
  ui.guestSessionSelect.disabled = true;
  replaceChildren(ui.guestSessionSelect, [new Option('Loading sessions…', '')]);
  const sessions = await fetchSessions();
  populateSessionSelect(ui.guestSessionSelect, sessions, { openOnly: true, includePlaceholder: true });
  if (!ui.guestSessionSelect.options.length || (ui.guestSessionSelect.options.length === 1 && !ui.guestSessionSelect.options[0].value)) {
    replaceChildren(ui.guestSessionSelect, [new Option('No open sessions', '')]);
    ui.guestSessionSelect.disabled = true;
  } else {
    ui.guestSessionSelect.disabled = false;
  }
  setFormMessage(ui.guestJoinMessage);
}

async function handleGuestJoin(event) {
  event.preventDefault();
  const sessionId = positiveInteger(ui.guestSessionSelect.value);
  if (!sessionId) {
    setFormMessage(ui.guestJoinMessage, 'Choose an open table session.', 'error');
    return;
  }
  if (!ui.guestQrToken.value.trim() && !ui.guestTestConfirm.checked) {
    setFormMessage(ui.guestJoinMessage, 'Check the local test confirmation or provide a QR token.', 'error');
    return;
  }
  const dietary = Array.from(ui.guestJoinForm.querySelectorAll('input[name="dietary"]:checked')).map((input) => input.value);
  setButtonBusy(ui.guestJoinButton, true, 'Joining…');
  setFormMessage(ui.guestJoinMessage, 'Verifying the table OTP…');
  try {
    const guest = await api(`/api/v1/table-sessions/${sessionId}/guests`, {
      method: 'POST',
      body: {
        display_name: ui.guestName.value.trim(),
        phone: ui.guestPhone.value.trim() || null,
        otp: ui.guestOtp.value.trim(),
        qr_token: ui.guestQrToken.value.trim() || null,
        test_qr_verified: ui.guestTestConfirm.checked,
        dietary_preferences: dietary,
      },
      timeout: 15000,
    });
    setFormMessage(ui.guestJoinMessage, `Guest session #${guest?.id || ''} joined successfully.`, 'success');
    showToast(`${guest?.display_name || 'Test guest'} joined session #${sessionId}.`, 'success');
    ui.guestOtp.value = '';
    ui.guestQrToken.value = '';
    ui.guestPhone.value = '';
    ui.guestTestConfirm.checked = false;
  } catch (error) {
    setFormMessage(ui.guestJoinMessage, error.message, 'error');
  } finally {
    setButtonBusy(ui.guestJoinButton, false);
  }
}

function resetChat(announce = false) {
  state.jarvisSessionId = createSessionId();
  const welcome = createElement('div', 'chat-message chat-message--bot');
  const avatar = createElement('span', 'message-avatar', 'J');
  const body = createElement('div', 'message-body');
  body.append(createElement('p', '', 'Good evening. I’m connected to this branch’s live operating context. What would you like to improve tonight?'));
  welcome.append(avatar, body);
  replaceChildren(ui.chatLog, welcome);
  if (announce) showToast('Started a new JARVIS thread.', 'info');
}

function scrollChatToBottom() {
  ui.chatLog.scrollTop = ui.chatLog.scrollHeight;
}

function chatMessage(role, text, recommendations = []) {
  const isUser = role === 'user';
  const message = createElement('div', `chat-message ${isUser ? 'chat-message--user' : 'chat-message--bot'}`);
  const avatar = createElement('span', 'message-avatar', isUser ? state.role.slice(0, 1).toUpperCase() : 'J');
  const body = createElement('div', 'message-body');
  body.append(createElement('p', '', text));
  const validRecommendations = asArray(recommendations).filter((item) => item && typeof item === 'object');
  if (validRecommendations.length) {
    const list = createElement('div', 'recommendation-list');
    validRecommendations.forEach((recommendation) => {
      const card = createElement('div', 'recommendation');
      const top = createElement('div', 'recommendation__top');
      top.append(
        createElement('strong', '', recommendation.title || 'Recommended action'),
        createElement('span', `priority priority--${priorityClass(recommendation.priority)}`, humanize(recommendation.priority || 'medium')),
      );
      const details = [recommendation.action, recommendation.rationale].filter(Boolean).join(' — ');
      card.append(top, createElement('p', '', details));
      list.append(card);
    });
    body.append(list);
  }
  body.append(createElement('time', 'message-time', new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })));
  message.append(avatar, body);
  return message;
}

function chatLoadingMessage() {
  const message = createElement('div', 'chat-message chat-message--bot');
  const avatar = createElement('span', 'message-avatar', 'J');
  const body = createElement('div', 'message-body');
  const dots = createElement('span', 'typing-dots');
  dots.append(createElement('i'), createElement('i'), createElement('i'));
  body.append(dots);
  message.append(avatar, body);
  return message;
}

async function sendChatMessage(query) {
  const cleanQuery = String(query || '').trim();
  if (!cleanQuery) return;
  ui.chatForm.querySelector('button[type="submit"]').disabled = true;
  ui.chatInput.disabled = true;
  ui.chatLog.append(chatMessage('user', cleanQuery));
  const loading = chatLoadingMessage();
  ui.chatLog.append(loading);
  scrollChatToBottom();

  try {
    const response = await api('/api/v1/jarvis/execute', {
      method: 'POST',
      body: {
        role: state.role,
        branch_id: state.branchId,
        user_query: cleanQuery,
        context_payload: {
          active_orders: [],
          station_queues: {},
          inventory_alerts: [],
        },
        session_id: state.jarvisSessionId,
      },
      timeout: 130000,
    });
    if (response && response.session_id) state.jarvisSessionId = String(response.session_id);
    const text = response && response.summary
      ? String(response.summary)
      : 'JARVIS completed the request, but returned no narrative summary.';
    loading.replaceWith(chatMessage('assistant', text, response?.recommendations));
  } catch (error) {
    loading.replaceWith(chatMessage('assistant', `I could not complete that briefing. ${error.message}`));
  } finally {
    ui.chatForm.querySelector('button[type="submit"]').disabled = false;
    ui.chatInput.disabled = false;
    ui.chatInput.focus();
    scrollChatToBottom();
  }
}

async function requestDevToken(bootstrap, branchId, role) {
  const secret = String(bootstrap || '').trim();
  if (!secret) throw new ApiError('Enter the local DEV_AUTH_TOKEN first.', 400);
  const response = await api('/api/v1/auth/dev-token', {
    method: 'POST',
    auth: false,
    headers: { 'X-Dev-Bootstrap': secret },
    body: { user_id: 1, org_id: 1, branch_id: branchId, role },
    timeout: 10000,
  });
  if (!response?.access_token) throw new ApiError('The backend did not return an access token.', 502);
  return response.access_token;
}

async function bootstrapDevAccess() {
  const branchId = positiveInteger(ui.accessBranchInput.value) || state.branchId;
  const role = VALID_ROLES.has(ui.accessRoleSelect.value) ? ui.accessRoleSelect.value : state.role;
  setButtonBusy(ui.devBootstrapButton, true, 'Bootstrapping…');
  try {
    const token = await requestDevToken(ui.bootstrapInput.value, branchId, role);
    await bootstrapAccess({ token, branchId, role, keepToken: true, notify: false });
    ui.bootstrapInput.value = '';
    showToast('Local authenticated console session connected.', 'success');
  } catch (error) {
    setFormMessage(ui.accessMessage, error.message, 'error');
    showToast(error.message, 'error');
  } finally {
    setButtonBusy(ui.devBootstrapButton, false);
  }
}

async function bootstrapAccess({ token, branchId, role, keepToken, notify = true } = {}) {
  state.token = normalizeToken(token);
  state.branchId = positiveInteger(branchId) || state.branchId;
  state.role = VALID_ROLES.has(role) ? role : state.role;
  storageSet('token', keepToken ? state.token : null);
  storageSet('branchId', state.branchId);
  storageSet('role', state.role);
  state.jarvisSessionId = createSessionId();
  updateContextUi();
  setFormMessage(ui.accessMessage, 'Checking the backend connection…');
  setButtonBusy(ui.accessSubmitButton, true, 'Connecting…');

  try {
    await checkHealth();
    try {
      await loadBranches();
    } catch (branchError) {
      populateBranchSelect();
      showToast(`Branch catalog unavailable: ${branchError.message}`, 'warning');
    }
    await refreshCurrentView({ notify: false });
    setFormMessage(ui.accessMessage, 'Connected. Operational context is ready.', 'success');
    if (ui.accessDialog.open) ui.accessDialog.close();
    if (notify) showToast(state.token ? 'Authenticated console session connected.' : 'Development bootstrap connected.', 'success');
  } catch (error) {
    setFormMessage(ui.accessMessage, error.message, 'error');
    if (notify) showToast(error.message, 'error');
  } finally {
    setButtonBusy(ui.accessSubmitButton, false);
  }
}

function openAccessDialog() {
  ui.accessBranchInput.value = String(state.branchId);
  ui.accessRoleSelect.value = state.role;
  ui.tokenInput.value = '';
  ui.sessionTokenCheck.checked = Boolean(state.token);
  setFormMessage(ui.accessMessage);
  if (typeof ui.accessDialog.showModal === 'function') ui.accessDialog.showModal();
  else ui.accessDialog.setAttribute('open', '');
  window.setTimeout(() => ui.tokenInput.focus(), 0);
}

function closeAccessDialog() {
  if (ui.accessDialog.open && typeof ui.accessDialog.close === 'function') ui.accessDialog.close();
  else ui.accessDialog.removeAttribute('open');
}

function bindEvents() {
  document.querySelectorAll('[data-view-target]').forEach((button) => {
    button.addEventListener('click', () => showView(button.dataset.viewTarget));
  });

  document.querySelectorAll('[data-prompt]').forEach((button) => {
    button.addEventListener('click', () => sendChatMessage(button.dataset.prompt));
  });
  document.querySelectorAll('[data-jump-chat]').forEach((button) => {
    button.addEventListener('click', () => {
      showView('overview');
      window.setTimeout(() => ui.chatInput.focus(), 100);
    });
  });

  ui.menuButton.addEventListener('click', openMobileNav);
  ui.sidebarClose.addEventListener('click', closeMobileNav);
  ui.sidebarScrim.addEventListener('click', closeMobileNav);
  ui.refreshButton.addEventListener('click', () => refreshCurrentView());
  ui.refreshTablesButton.addEventListener('click', () => refreshCurrentView());
  ui.refreshOrdersButton.addEventListener('click', () => refreshCurrentView());
  ui.refreshPaymentsButton.addEventListener('click', () => refreshCurrentView());

  ui.branchSelect.addEventListener('change', () => {
    const nextBranch = positiveInteger(ui.branchSelect.value);
    if (!nextBranch || nextBranch === state.branchId) return;
    state.branchId = nextBranch;
    storageSet('branchId', state.branchId);
    state.jarvisSessionId = createSessionId();
    ui.paymentSessionSelect.value = '';
    updateContextUi();
    resetChat();
    refreshCurrentView();
  });

  ui.roleSelect.addEventListener('change', () => {
    const nextRole = ui.roleSelect.value;
    if (!VALID_ROLES.has(nextRole)) return;
    if (state.token) {
      showToast('Role is part of the signed token. Reconnect to change the role lens.', 'info');
      openAccessDialog();
      return;
    }
    state.role = nextRole;
    storageSet('role', state.role);
    state.jarvisSessionId = createSessionId();
    updateContextUi();
    resetChat();
  });

  ui.tableSearch.addEventListener('input', () => {
    state.tableQuery = ui.tableSearch.value;
    renderTables();
  });
  ui.tableGrid.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-table-id]');
    if (button) handleOpenSession(button);
  });

  ui.orderSearch.addEventListener('input', () => {
    state.orderQuery = ui.orderSearch.value;
    renderOrders();
  });
  ui.orderStatusFilter.addEventListener('change', () => {
    state.orderStatus = ui.orderStatusFilter.value;
    renderOrders();
  });

  ui.paymentSessionSelect.addEventListener('change', async () => {
    startSync();
    try {
      await loadPaymentSession();
    } catch (error) {
      replaceChildren(ui.paymentList, errorState(error, () => loadPaymentSession()));
      showToast(error.message, 'error');
    } finally {
      endSync();
    }
  });
  ui.paymentOrderSelect.addEventListener('change', () => {
    const order = state.paymentOrders.find((entry) => String(entry.id) === ui.paymentOrderSelect.value);
    if (!order) {
      ui.paymentAmount.value = '';
      ui.postPaymentButton.disabled = true;
      return;
    }
    const remaining = remainingForOrder(order.id);
    ui.paymentAmount.value = remaining > 0 ? remaining.toFixed(2) : '';
    ui.postPaymentButton.disabled = remaining <= 0;
    setFormMessage(ui.paymentFormMessage, remaining > 0 ? '' : 'This order is already covered by a recorded payment. Use Settle on the payment below.');
  });
  ui.paymentForm.addEventListener('submit', handlePaymentSubmit);
  ui.paymentList.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-payment-id]');
    if (button) handleSettlePayment(button);
  });

  ui.guestJoinForm.addEventListener('submit', handleGuestJoin);
  ui.newChatButton.addEventListener('click', () => resetChat(true));
  ui.chatForm.addEventListener('submit', (event) => {
    event.preventDefault();
    sendChatMessage(ui.chatInput.value);
    ui.chatInput.value = '';
  });
  ui.chatInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      ui.chatForm.requestSubmit();
    }
  });

  ui.openAccessButton.addEventListener('click', openAccessDialog);
  ui.closeAccessButton.addEventListener('click', closeAccessDialog);
  ui.accessDialog.addEventListener('close', () => {
    ui.tokenInput.value = '';
  });
  ui.toggleTokenButton.addEventListener('click', () => {
    const show = ui.tokenInput.type === 'password';
    ui.tokenInput.type = show ? 'text' : 'password';
    ui.toggleTokenButton.textContent = show ? 'Hide' : 'Show';
    ui.toggleTokenButton.setAttribute('aria-label', show ? 'Hide token' : 'Show token');
  });
  ui.accessForm.addEventListener('submit', (event) => {
    event.preventDefault();
    bootstrapAccess({
      token: ui.tokenInput.value,
      branchId: ui.accessBranchInput.value,
      role: ui.accessRoleSelect.value,
      keepToken: ui.sessionTokenCheck.checked,
    });
  });
  ui.devBootstrapButton.addEventListener('click', bootstrapDevAccess);
  ui.clearTokenButton.addEventListener('click', () => {
    state.token = '';
    storageSet('token', null);
    ui.tokenInput.value = '';
    updateContextUi();
    setFormMessage(ui.accessMessage, 'Saved token cleared. Use dev bootstrap or connect with a new token.', 'success');
    showToast('Bearer token cleared from this session.', 'success');
  });
}

function initialHealthCheck() {
  checkHealth().catch(() => {
    // The disconnected state is already rendered by checkHealth.
  });
}

function init() {
  bindEvents();
  updateContextUi();
  resetChat();
  const initialView = window.location.hash.replace(/^#/, '');
  showView(VIEW_META[initialView] ? initialView : 'overview');
  window.setInterval(updateClock, 30000);
  initialHealthCheck();
  if (state.token) {
    loadBranches().catch(() => populateBranchSelect());
    refreshCurrentView({ notify: false });
  } else {
    showToast('Connect through the access dialog to load protected operational routes.', 'info');
  }
}

init();
