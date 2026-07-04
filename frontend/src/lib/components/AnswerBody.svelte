<script>
	// Renders the Copilot's narrated answer: safe markdown for prose PLUS a small set of
	// visualization blocks the model can emit as fenced code blocks with a special tag —
	// ```callout / ```stats / ```progress / ```compare (JSON body). Everything is escaped
	// before rendering; the blocks are drawn as real components, never via {@html}.
	import {
		CheckCircle2,
		AlertTriangle,
		Info,
		Lightbulb,
		TrendingUp,
		TrendingDown,
		ArrowRight
	} from '@lucide/svelte';

	let { text } = $props();

	const SPECIAL = new Set(['callout', 'stats', 'stat', 'progress', 'compare']);
	const blocks = $derived(parse(text ?? ''));

	// ── Block splitter: separate fenced regions from markdown prose ─────────────
	function parse(src) {
		const lines = src.split('\n');
		const out = [];
		let buf = [];
		const flush = () => {
			if (buf.join('').trim()) out.push({ type: 'md', text: buf.join('\n') });
			buf = [];
		};
		let i = 0;
		while (i < lines.length) {
			const fence = lines[i].match(/^```([\w-]+)?\s*$/);
			if (fence) {
				flush();
				const lang = (fence[1] || '').toLowerCase();
				i++;
				const body = [];
				while (i < lines.length && !/^```\s*$/.test(lines[i])) body.push(lines[i++]);
				i++; // closing fence
				if (SPECIAL.has(lang)) {
					let data = null;
					try {
						data = JSON.parse(body.join('\n'));
					} catch {
						data = null;
					}
					out.push(
						data == null
							? { type: 'code', body: body.join('\n') }
							: { type: 'block', kind: lang === 'stat' ? 'stats' : lang, data }
					);
				} else {
					out.push({ type: 'code', body: body.join('\n') });
				}
			} else {
				buf.push(lines[i++]);
			}
		}
		flush();
		return out;
	}

	// ── Safe markdown → HTML (escape-first) ─────────────────────────────────────
	function esc(s) {
		return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
	}
	function inline(s) {
		let t = esc(s);
		t = t.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
		t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
		t = t.replace(/(^|[^*])\*([^*\s][^*]*?)\*/g, '$1<em>$2</em>');
		t = t.replace(
			/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
			'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
		);
		return t;
	}
	function splitRow(r) {
		return r.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
	}
	const BLOCK_START = /^(#{1,4}\s|>\s?|\s*[-*+]\s|\s*\d+\.\s|```)/;
	function renderMarkdown(src) {
		const lines = src.split('\n');
		const html = [];
		let i = 0;
		while (i < lines.length) {
			const line = lines[i];
			if (/^\s*$/.test(line)) {
				i++;
				continue;
			}
			const h = line.match(/^(#{1,4})\s+(.*)$/);
			if (h) {
				const lvl = Math.min(6, h[1].length + 2);
				html.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
				i++;
				continue;
			}
			if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
				html.push('<hr/>');
				i++;
				continue;
			}
			if (/^\s*>\s?/.test(line)) {
				const q = [];
				while (i < lines.length && /^\s*>\s?/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ''));
				html.push(`<blockquote>${inline(q.join(' '))}</blockquote>`);
				continue;
			}
			// GFM table: header row + separator row of dashes
			if (
				line.includes('|') &&
				i + 1 < lines.length &&
				/^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]) &&
				lines[i + 1].includes('-')
			) {
				const head = splitRow(line);
				i += 2;
				const rows = [];
				while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(splitRow(lines[i++]));
				const th = head.map((c) => `<th>${inline(c)}</th>`).join('');
				const body = rows
					.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`)
					.join('');
				html.push(`<table><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table>`);
				continue;
			}
			if (/^\s*\d+\.\s+/.test(line)) {
				const items = [];
				while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i]))
					items.push(`<li>${inline(lines[i++].replace(/^\s*\d+\.\s+/, ''))}</li>`);
				html.push(`<ol>${items.join('')}</ol>`);
				continue;
			}
			if (/^\s*[-*+]\s+/.test(line)) {
				const items = [];
				while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i]))
					items.push(`<li>${inline(lines[i++].replace(/^\s*[-*+]\s+/, ''))}</li>`);
				html.push(`<ul>${items.join('')}</ul>`);
				continue;
			}
			const para = [line];
			i++;
			while (i < lines.length && !/^\s*$/.test(lines[i]) && !BLOCK_START.test(lines[i]) && !/^\s*([-*_])\1{2,}\s*$/.test(lines[i]))
				para.push(lines[i++]);
			html.push(`<p>${inline(para.join(' '))}</p>`);
		}
		return html.join('');
	}

	// ── Block helpers ───────────────────────────────────────────────────────────
	const CALLOUT_ICON = {
		good: CheckCircle2,
		bad: AlertTriangle,
		warn: AlertTriangle,
		info: Info,
		tip: Lightbulb
	};
	function toneClass(t) {
		return ['good', 'bad', 'warn', 'info', 'neutral', 'tip'].includes(t) ? t : 'info';
	}
	function asPct(v) {
		if (typeof v === 'string') v = parseFloat(v.replace('%', ''));
		if (typeof v !== 'number' || isNaN(v)) return 0;
		if (v <= 1) v *= 100;
		return Math.max(0, Math.min(100, v));
	}
	const asArray = (d) => (Array.isArray(d) ? d : [d]);
</script>

{#each blocks as b}
	{#if b.type === 'md'}
		<div class="md">{@html renderMarkdown(b.text)}</div>
	{:else if b.type === 'code'}
		<pre class="code">{b.body}</pre>
	{:else if b.kind === 'callout'}
		{@const tone = toneClass(b.data.tone)}
		{@const Icon = CALLOUT_ICON[tone] ?? Info}
		<div class="callout {tone}">
			<span class="cico"><Icon size={17} strokeWidth={2} /></span>
			<div class="cbody">
				{#if b.data.title}<div class="ctitle">{b.data.title}</div>{/if}
				<div class="ctext">{b.data.text}</div>
			</div>
		</div>
	{:else if b.kind === 'stats'}
		<div class="statrow">
			{#each asArray(b.data) as s}
				<div class="stat {toneClass(s.tone)}">
					<div class="slab">{s.label}</div>
					<div class="sval">{s.value}</div>
					{#if s.sub}<div class="ssub">{s.sub}</div>{/if}
				</div>
			{/each}
		</div>
	{:else if b.kind === 'progress'}
		{@const tone = toneClass(b.data.tone)}
		<div class="prog">
			<div class="ptop">
				<span class="plab">{b.data.label}</span>
				<span class="pval {tone}">{b.data.value}{typeof b.data.value === 'number' ? '%' : ''}</span>
			</div>
			<div class="pbar"><div class="pfill {tone}" style="width:{asPct(b.data.value)}%"></div></div>
			{#if b.data.caption}<div class="pcap">{b.data.caption}</div>{/if}
		</div>
	{:else if b.kind === 'compare'}
		{@const tone = toneClass(b.data.tone)}
		<div class="cmp">
			<div class="cmplab">{b.data.label}</div>
			<div class="cmprow">
				<span class="was">{b.data.before}</span>
				<ArrowRight size={15} strokeWidth={2} />
				<span class="now {tone}">
					{#if tone === 'good'}<TrendingUp size={14} />{:else if tone === 'bad'}<TrendingDown size={14} />{/if}
					{b.data.after}
				</span>
			</div>
		</div>
	{/if}
{/each}

<style>
	.md {
		font-size: 14.5px;
		color: var(--ink);
		line-height: 1.65;
	}
	.md :global(p) {
		margin: 0 0 9px;
	}
	.md :global(h3),
	.md :global(h4),
	.md :global(h5),
	.md :global(h6) {
		font-family: var(--font-serif);
		font-weight: 700;
		color: var(--ink);
		margin: 14px 0 7px;
		line-height: 1.2;
	}
	.md :global(h3) {
		font-size: 19px;
	}
	.md :global(h4) {
		font-size: 16px;
	}
	.md :global(h5),
	.md :global(h6) {
		font-size: 14px;
	}
	.md :global(ul),
	.md :global(ol) {
		margin: 0 0 9px;
		padding-left: 20px;
	}
	.md :global(li) {
		margin: 3px 0;
	}
	.md :global(strong) {
		color: var(--brand-strong);
	}
	.md :global(em) {
		color: var(--ink-2);
	}
	.md :global(code) {
		font-family: ui-monospace, monospace;
		font-size: 12.5px;
		background: var(--card-2);
		border: 1px solid var(--primary-200);
		padding: 1px 5px;
	}
	.md :global(a) {
		color: var(--brand-strong);
		text-decoration: underline;
	}
	.md :global(hr) {
		border: none;
		border-top: 1px solid var(--primary-300);
		margin: 14px 0;
	}
	.md :global(blockquote) {
		margin: 0 0 10px;
		padding: 4px 0 4px 14px;
		border-left: 3px solid var(--primary-300);
		color: var(--mut);
		font-style: italic;
	}
	.md :global(table) {
		width: 100%;
		border-collapse: collapse;
		margin: 4px 0 12px;
		font-size: 13px;
	}
	.md :global(th) {
		text-align: left;
		font-family: var(--font-sans);
		font-size: 10px;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--mut);
		padding: 0 8px 7px;
		font-weight: 700;
	}
	.md :global(td) {
		padding: 6px 8px;
		border-top: 1px solid var(--primary-200);
		font-variant-numeric: tabular-nums;
	}
	.code {
		font-family: ui-monospace, monospace;
		font-size: 12px;
		background: var(--card-2);
		border: 1px solid var(--primary-200);
		padding: 10px 12px;
		overflow-x: auto;
		margin: 8px 0;
	}

	/* ── Callout ── */
	.callout {
		display: flex;
		gap: 11px;
		padding: 12px 14px;
		margin: 10px 0;
		border: 1.5px solid var(--primary-800);
		border-left-width: 5px;
		background: var(--card);
		box-shadow: var(--shadow-stamp-sm);
	}
	.callout.good {
		border-left-color: var(--inflow);
	}
	.callout.bad {
		border-left-color: var(--outflow);
	}
	.callout.warn {
		border-left-color: var(--primary-600);
	}
	.callout.info,
	.callout.neutral,
	.callout.tip {
		border-left-color: var(--brand);
	}
	.cico {
		flex: none;
		margin-top: 1px;
	}
	.callout.good .cico {
		color: var(--inflow);
	}
	.callout.bad .cico {
		color: var(--outflow);
	}
	.callout.warn .cico {
		color: var(--primary-700);
	}
	.callout.info .cico,
	.callout.neutral .cico,
	.callout.tip .cico {
		color: var(--brand);
	}
	.ctitle {
		font-family: var(--font-sans);
		font-weight: 700;
		font-size: 11px;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--ink);
		margin-bottom: 3px;
	}
	.ctext {
		font-size: 14px;
		color: var(--ink);
		line-height: 1.5;
	}

	/* ── Stat tiles ── */
	.statrow {
		display: flex;
		flex-wrap: wrap;
		gap: 10px;
		margin: 10px 0;
	}
	.stat {
		flex: 1 1 130px;
		border: 1.5px solid var(--primary-800);
		border-left-width: 5px;
		box-shadow: var(--shadow-stamp-sm);
		background: var(--card);
		padding: 10px 13px;
	}
	.stat.good {
		border-left-color: var(--inflow);
	}
	.stat.bad {
		border-left-color: var(--outflow);
	}
	.stat.warn {
		border-left-color: var(--primary-600);
	}
	.stat.info,
	.stat.neutral,
	.stat.tip {
		border-left-color: var(--primary-900);
	}
	.slab {
		font-family: var(--font-sans);
		font-size: 10px;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--mut);
		font-weight: 600;
	}
	.sval {
		font-family: var(--font-serif);
		font-size: 27px;
		font-weight: 800;
		line-height: 1.05;
		margin-top: 5px;
		font-variant-numeric: tabular-nums;
	}
	.stat.good .sval {
		color: var(--inflow);
	}
	.stat.bad .sval {
		color: var(--outflow);
	}
	.stat.warn .sval {
		color: var(--primary-700);
	}
	.ssub {
		font-size: 11.5px;
		color: var(--faint);
		margin-top: 3px;
	}

	/* ── Progress bar ── */
	.prog {
		margin: 10px 0;
	}
	.ptop {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		margin-bottom: 4px;
	}
	.plab {
		font-size: 13px;
		font-weight: 600;
		color: var(--ink-2);
	}
	.pval {
		font-weight: 700;
		font-variant-numeric: tabular-nums;
	}
	.pval.good {
		color: var(--inflow);
	}
	.pval.bad {
		color: var(--outflow);
	}
	.pval.warn {
		color: var(--primary-700);
	}
	.pbar {
		height: 9px;
		background: var(--card-2);
		border: 1.5px solid var(--primary-800);
		overflow: hidden;
	}
	.pfill {
		height: 100%;
		background: var(--brand);
	}
	.pfill.good {
		background: var(--inflow);
	}
	.pfill.bad {
		background: var(--outflow);
	}
	.pfill.warn {
		background: var(--primary-600);
	}
	.pcap {
		font-size: 11.5px;
		color: var(--faint);
		margin-top: 4px;
	}

	/* ── Compare (before → after) ── */
	.cmp {
		margin: 10px 0;
		border: 1.5px solid var(--primary-800);
		box-shadow: var(--shadow-stamp-sm);
		background: var(--card);
		padding: 10px 14px;
	}
	.cmplab {
		font-family: var(--font-sans);
		font-size: 10px;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--mut);
		font-weight: 600;
		margin-bottom: 5px;
	}
	.cmprow {
		display: flex;
		align-items: center;
		gap: 12px;
		font-family: var(--font-serif);
		font-size: 22px;
		font-weight: 800;
		font-variant-numeric: tabular-nums;
	}
	.cmprow .was {
		color: var(--mut);
	}
	.cmprow .now {
		display: inline-flex;
		align-items: center;
		gap: 5px;
	}
	.cmprow .now.good {
		color: var(--inflow);
	}
	.cmprow .now.bad {
		color: var(--outflow);
	}
	.cmprow .now.warn {
		color: var(--primary-700);
	}
</style>
