// Fetch wrappers for the AdvisorOS read APIs + formatting helpers.
// Defaults to same-origin /api, which is where FastAPI serves both the built SPA and
// the API in the single-container deploy. Local `vite dev` overrides this to point at
// the standalone FastAPI dev server — see .env.development.
const API_BASE = import.meta.env.VITE_API_BASE ?? '/api';

async function get(path, params) {
	const url = new URL(API_BASE + path, window.location.origin);
	if (params) {
		for (const [k, v] of Object.entries(params)) {
			if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
		}
	}
	const res = await fetch(url);
	if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
	return res.json();
}

async function post(path, body) {
	const res = await fetch(API_BASE + path, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body ?? {})
	});
	if (!res.ok) {
		let detail = `${res.status} ${res.statusText}`;
		try {
			const j = await res.json();
			if (j?.detail) detail = j.detail;
		} catch {
			/* non-JSON error body */
		}
		throw new Error(detail);
	}
	return res.json();
}

async function del(path) {
	const res = await fetch(API_BASE + path, { method: 'DELETE' });
	if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
	return res.json();
}

// Absolute SSE URL for a Copilot job — EventSource needs a full path, not a relative
// one resolved against a different base.
export function copilotJobEventsUrl(jobId) {
	return new URL(`${API_BASE}/copilot/jobs/${jobId}/events`, window.location.origin).toString();
}

export const api = {
	listClients: (params) => get('/clients', params),
	getClient: (id) => get(`/clients/${id}`),
	getHoldings: (id) => get(`/clients/${id}/holdings`),
	getSips: (id) => get(`/clients/${id}/sips`),
	getTransactions: (id) => get(`/clients/${id}/transactions`),
	getInsights: (id) => get(`/clients/${id}/insights`),
	bookSummary: () => get('/book/summary'),
	bookRadar: () => get('/book/radar'),
	bookInsights: () => get('/book/insights'),
	refreshBookInsights: () => post('/book/insights/refresh'),
	bookTrend: () => get('/book/trend'),
	bookAllocationTrend: () => get('/book/allocation-trend'),
	// Copilot: one message in, a job back immediately — the caller opens
	// copilotJobEventsUrl(job_id) to stream tool calls/reasoning/the final answer.
	startCopilotJob: (body) => post('/copilot/jobs', body),
	stopCopilotJob: (jobId) => del(`/copilot/jobs/${jobId}`),
	// Commit half of the NL data-entry flow (add_transactions only parses).
	commitTransactions: (id, rows) => post(`/clients/${id}/transactions`, { rows }),
	// DB-backed conversation history.
	listConversations: () => get('/conversations'),
	getConversation: (id) => get(`/conversations/${id}`),
	deleteConversation: (id) => del(`/conversations/${id}`)
};

// ── Formatting ────────────────────────────────────────────────────────────────
// Indian-style short currency: ₹ Cr / L / K.
export function inr(n) {
	if (n == null) return '—';
	const abs = Math.abs(n);
	if (abs >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`;
	if (abs >= 1e5) return `₹${(n / 1e5).toFixed(2)} L`;
	if (abs >= 1e3) return `₹${(n / 1e3).toFixed(1)} K`;
	return `₹${n.toFixed(0)}`;
}

export function inrFull(n) {
	if (n == null) return '—';
	return '₹' + Math.round(n).toLocaleString('en-IN');
}

export function pct(x, digits = 0) {
	if (x == null) return '—';
	return `${(x * 100).toFixed(digits)}%`;
}

export function catLabel(cat) {
	return cat.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function fmtDate(iso) {
	if (!iso) return '—';
	return new Date(iso).toLocaleDateString('en-IN', {
		day: 'numeric',
		month: 'short',
		year: 'numeric'
	});
}

// A stable gold-family palette for the 14 categories (design.md: gold = identity).
const CATEGORY_ORDER = [
	'high_risk_equity',
	'mid_risk_equity',
	'low_risk_equity',
	'international_equity',
	'cash_equivalent',
	'good_debt',
	'bad_debt',
	'gold',
	'silver',
	'aggressive_hybrid',
	'balanced_advantage',
	'conservative_hybrid',
	'multi_asset',
	'other'
];
// Distinct hues per category so the allocation card reads clearly — still warm
// and ledger-toned, but each of the 14 is visually separable.
const PALETTE = [
	'#a8224a', // high_risk_equity — rose
	'#d9642a', // mid_risk_equity — burnt orange
	'#e0a80d', // low_risk_equity — gold
	'#2f8f83', // international_equity — teal
	'#7a8794', // cash_equivalent — slate
	'#4a7c3a', // good_debt — green
	'#7a1533', // bad_debt — deep maroon
	'#c9a227', // gold — amber
	'#9aa3af', // silver — cool grey
	'#b5401f', // aggressive_hybrid — clay red
	'#3a6ea5', // balanced_advantage — blue
	'#8a8f2e', // conservative_hybrid — olive
	'#7c5cbf', // multi_asset — violet
	'#8a6d3b' // other — brown
];
export function catColor(cat) {
	const i = CATEGORY_ORDER.indexOf(cat);
	return PALETTE[i < 0 ? PALETTE.length - 1 : i];
}
