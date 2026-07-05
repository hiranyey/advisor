// Parses the tiny {{tag:span}} emphasis markup the book-insights LLM emits in its
// headline/briefing text (see backend/app/llm/insights.py) into an HTML string for
// {@html}. Plain-text segments are escaped, so a stray literal "<" in model output can't
// inject markup — only the fixed set of tags below ever produces real elements.
const TAG_RE = /\{\{(hero|good|warn|bad|muted):([^}]+)\}\}/g;

function escapeHtml(s) {
	return s
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;');
}

export function renderRichHtml(text) {
	if (!text) return '';
	let out = '';
	let last = 0;
	for (const m of text.matchAll(TAG_RE)) {
		if (m.index > last) out += escapeHtml(text.slice(last, m.index));
		out += `<span class="rt rt-${m[1]}">${escapeHtml(m[2])}</span>`;
		last = m.index + m[0].length;
	}
	if (last < text.length) out += escapeHtml(text.slice(last));
	return out;
}
