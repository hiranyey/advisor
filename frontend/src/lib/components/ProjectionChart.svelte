<script>
	// Solid actual-value line up to today, joined to a future P5/P90 fan (P50 median
	// line down the middle) — the "portfolio health over N years" / SIP what-if chart.
	// Same inline-SVG technique as ValueChart.svelte, but x is a real time scale (not
	// index-spaced) since the historical series is monthly and the projection is yearly.
	import { inr, fmtDate } from '$lib/api.js';

	// history: [{date, value}] chronological, last point = "today".
	// projection: {dates, p5, p50, p90} — dates[0] repeats history's last date as the
	// join anchor (p5==p50==p90 there), then one point per future year.
	let { history = [], projection = null } = $props();

	const W = 640;
	const H = 260;
	const PAD = { l: 8, r: 8, t: 16, b: 26 };

	const hasProj = $derived(!!projection?.dates && projection.dates.length >= 2);

	const geom = $derived.by(() => {
		if (!history.length && !hasProj) return null;
		const t = (d) => new Date(d).getTime();
		const allDates = [...history.map((h) => h.date), ...(hasProj ? projection.dates.slice(1) : [])];
		if (allDates.length < 2) return null;

		const minT = t(allDates[0]);
		const maxT = t(allDates[allDates.length - 1]);
		const span = maxT - minT || 1;
		const allVals = [
			...history.map((h) => h.value),
			...(hasProj ? [...projection.p5, ...projection.p90] : [])
		];
		const max = Math.max(...allVals);
		const min = Math.min(...allVals, 0);

		const iw = W - PAD.l - PAD.r;
		const ih = H - PAD.t - PAD.b;
		const x = (d) => PAD.l + ((t(d) - minT) / span) * iw;
		const y = (v) => PAD.t + ih - ((v - min) / (max - min || 1)) * ih;

		const historyLine = history.map((h, i) => `${i === 0 ? 'M' : 'L'}${x(h.date)},${y(h.value)}`).join(' ');
		const historyArea = history.length
			? `${historyLine} L${x(history[history.length - 1].date)},${PAD.t + ih} L${x(history[0].date)},${PAD.t + ih} Z`
			: '';

		let p50Line = '';
		let bandArea = '';
		if (hasProj) {
			p50Line = projection.dates.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(d)},${y(projection.p50[i])}`).join(' ');
			const ups = projection.dates.map((d, i) => `${x(d)},${y(projection.p90[i])}`);
			const downs = projection.dates.map((d, i) => `${x(d)},${y(projection.p5[i])}`).reverse();
			bandArea = `M${ups.join('L')}L${downs.join('L')}Z`;
		}

		const joinDate = hasProj ? projection.dates[0] : history[history.length - 1]?.date;
		return { x, y, historyLine, historyArea, p50Line, bandArea, joinDate, iw, ih, allDates };
	});

	// Unified samples for hover: historical points (actual value) + future projection
	// points (median + range). The join date is only represented once, as a history point.
	const samples = $derived.by(() => {
		const hist = history.map((h) => ({ date: h.date, kind: 'hist', value: h.value }));
		const proj = hasProj
			? projection.dates
					.slice(1)
					.map((d, i) => ({ date: d, kind: 'proj', p5: projection.p5[i + 1], p50: projection.p50[i + 1], p90: projection.p90[i + 1] }))
			: [];
		return [...hist, ...proj];
	});

	let hoverIdx = $state(null);
	const hp = $derived(hoverIdx != null ? samples[hoverIdx] : null);
	const tipLeft = $derived(geom && hp ? (geom.x(hp.date) / W) * 100 : 0);
	const tipAlign = $derived(tipLeft < 20 ? 'start' : tipLeft > 80 ? 'end' : 'center');

	function onMove(e) {
		if (!geom || !samples.length) return;
		const rect = e.currentTarget.getBoundingClientRect();
		const svgX = ((e.clientX - rect.left) / rect.width) * W;
		let best = 0;
		let bestDist = Infinity;
		samples.forEach((s, i) => {
			const dist = Math.abs(geom.x(s.date) - svgX);
			if (dist < bestDist) {
				bestDist = dist;
				best = i;
			}
		});
		hoverIdx = best;
	}
	function onLeave() {
		hoverIdx = null;
	}
</script>

{#if geom}
	<div class="legend">
		<span class="lg"><i class="sw val"></i> Value</span>
		{#if hasProj}
			<span class="lg"><i class="sw med"></i> Projected (median)</span>
			<span class="lg"><i class="sw band"></i> P5–P90 range</span>
		{/if}
	</div>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="chartwrap"
		role="img"
		aria-label="portfolio value to date and projected range"
		onpointermove={onMove}
		onpointerleave={onLeave}
	>
		<svg viewBox="0 0 {W} {H}" class="pc">
			<defs>
				<linearGradient id="pcfill" x1="0" y1="0" x2="0" y2="1">
					<stop offset="0" stop-color="#b8860b" stop-opacity="0.22" />
					<stop offset="1" stop-color="#b8860b" stop-opacity="0.03" />
				</linearGradient>
			</defs>
			<g stroke="#fbe497" stroke-width="1">
				<line x1={PAD.l} y1={PAD.t} x2={W - PAD.r} y2={PAD.t} />
				<line x1={PAD.l} y1={(PAD.t + H - PAD.b) / 2} x2={W - PAD.r} y2={(PAD.t + H - PAD.b) / 2} />
				<line x1={PAD.l} y1={H - PAD.b} x2={W - PAD.r} y2={H - PAD.b} />
			</g>

			{#if hasProj}
				<path d={geom.bandArea} class="band" />
			{/if}
			{#if geom.historyArea}
				<path d={geom.historyArea} fill="url(#pcfill)" />
			{/if}
			{#if hasProj}
				<path d={geom.p50Line} class="p50line" />
			{/if}
			{#if geom.historyLine}
				<path d={geom.historyLine} class="valline" />
			{/if}

			{#if geom.joinDate}
				<line
					x1={geom.x(geom.joinDate)}
					y1={PAD.t}
					x2={geom.x(geom.joinDate)}
					y2={H - PAD.b}
					class="joinline"
				/>
				<text x={geom.x(geom.joinDate) + 4} y={PAD.t + 10} class="joinlabel">Today</text>
			{/if}

			{#if hoverIdx != null && hp}
				<line x1={geom.x(hp.date)} y1={PAD.t} x2={geom.x(hp.date)} y2={H - PAD.b} class="guide" />
				{#if hp.kind === 'hist'}
					<circle cx={geom.x(hp.date)} cy={geom.y(hp.value)} r="4.5" class="dot val" />
				{:else}
					<circle cx={geom.x(hp.date)} cy={geom.y(hp.p90)} r="3.5" class="dot band" />
					<circle cx={geom.x(hp.date)} cy={geom.y(hp.p5)} r="3.5" class="dot band" />
					<circle cx={geom.x(hp.date)} cy={geom.y(hp.p50)} r="4.5" class="dot med" />
				{/if}
			{/if}

			<g font-family="Inter, sans-serif" font-size="11" fill="#d4a017">
				<text x={PAD.l} y={H - 8}>{fmtDate(geom.allDates[0])}</text>
				<text x={W - PAD.r} y={H - 8} text-anchor="end">{fmtDate(geom.allDates[geom.allDates.length - 1])}</text>
			</g>
		</svg>

		{#if hp}
			<div class="tip" style="left:{tipLeft}%" data-align={tipAlign}>
				<div class="tipdate">{fmtDate(hp.date)}</div>
				{#if hp.kind === 'hist'}
					<div class="tiprow">
						<span class="k"><i class="sw val"></i> Value</span>
						<span class="v">{inr(hp.value)}</span>
					</div>
				{:else}
					<div class="tiprow">
						<span class="k"><i class="sw med"></i> Median</span>
						<span class="v">{inr(hp.p50)}</span>
					</div>
					<div class="tiprow">
						<span class="k">Range</span>
						<span class="v">{inr(hp.p5)} – {inr(hp.p90)}</span>
					</div>
				{/if}
			</div>
		{/if}
	</div>
{:else}
	<p class="dim">Not enough data to chart.</p>
{/if}

<style>
	.chartwrap {
		position: relative;
	}
	.pc {
		width: 100%;
		height: auto;
		display: block;
	}
	.legend {
		display: flex;
		gap: 16px;
		flex-wrap: wrap;
		margin-bottom: 10px;
		font-size: 12px;
		color: var(--mut);
		font-weight: 500;
	}
	.lg {
		display: inline-flex;
		align-items: center;
		gap: 6px;
	}
	.sw {
		width: 14px;
		height: 0;
		display: inline-block;
		border-top-width: 2.5px;
		border-top-style: solid;
	}
	.sw.val {
		border-top-color: var(--brand-strong);
	}
	.sw.med {
		border-top-color: var(--brand-strong);
		border-top-style: dashed;
	}
	.sw.band {
		border-top: none;
		height: 8px;
		background: var(--brand);
		opacity: 0.18;
	}
	.valline {
		fill: none;
		stroke: var(--brand-strong);
		stroke-width: 2.5;
	}
	.p50line {
		fill: none;
		stroke: var(--brand-strong);
		stroke-width: 2;
		stroke-dasharray: 5 4;
		opacity: 0.85;
	}
	.band {
		fill: var(--brand);
		opacity: 0.16;
		stroke: none;
	}
	.joinline {
		stroke: var(--primary-900);
		stroke-width: 1.25;
		stroke-dasharray: 4 3;
		opacity: 0.6;
	}
	.joinlabel {
		font-family: Inter, sans-serif;
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		fill: var(--primary-900);
		opacity: 0.7;
	}
	.guide {
		stroke: var(--brand-strong);
		stroke-width: 1;
		stroke-dasharray: 3 3;
		opacity: 0.5;
	}
	.dot.val {
		fill: var(--brand-strong);
		stroke: var(--paper);
		stroke-width: 1.5;
	}
	.dot.med {
		fill: var(--brand-strong);
		stroke: var(--paper);
		stroke-width: 1.5;
	}
	.dot.band {
		fill: var(--brand);
		stroke: var(--paper);
		stroke-width: 1.5;
	}

	/* Hover tooltip */
	.tip {
		position: absolute;
		top: 4px;
		pointer-events: none;
		background: var(--paper);
		border: 1.5px solid var(--primary-800);
		box-shadow: var(--shadow-stamp-sm);
		padding: 8px 10px;
		min-width: 150px;
		z-index: 3;
	}
	.tip[data-align='center'] {
		transform: translateX(-50%);
	}
	.tip[data-align='end'] {
		transform: translateX(-100%);
	}
	.tip[data-align='start'] {
		transform: translateX(0);
	}
	.tipdate {
		font-family: var(--font-sans);
		font-size: 10.5px;
		font-weight: 600;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--mut);
		margin-bottom: 6px;
	}
	.tiprow {
		display: flex;
		justify-content: space-between;
		gap: 18px;
		align-items: center;
		font-size: 13px;
		padding: 2px 0;
	}
	.tiprow .k {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		color: var(--ink-2);
		white-space: nowrap;
	}
	.tiprow .v {
		font-variant-numeric: tabular-nums;
		font-weight: 600;
		color: var(--ink);
		white-space: nowrap;
	}
	.dim {
		font-size: 12px;
		color: var(--faint);
	}
</style>
