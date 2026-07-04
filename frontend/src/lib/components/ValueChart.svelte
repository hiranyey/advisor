<script>
	import { inr, fmtDate } from '$lib/api.js';

	// points: [{ date, value, invested }]
	let { points = [] } = $props();

	const W = 560;
	const H = 240;
	const PAD = { l: 8, r: 8, t: 16, b: 24 };

	const geom = $derived.by(() => {
		if (points.length < 2) return null;
		const vals = points.flatMap((p) => [p.value, p.invested ?? 0]);
		const max = Math.max(...vals);
		const min = Math.min(...vals, 0);
		const iw = W - PAD.l - PAD.r;
		const ih = H - PAD.t - PAD.b;
		const x = (i) => PAD.l + (i / (points.length - 1)) * iw;
		const y = (v) => PAD.t + ih - ((v - min) / (max - min || 1)) * ih;
		const path = (key) =>
			points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i)},${y(p[key] ?? 0)}`).join(' ');
		const valueLine = path('value');
		const area = `${valueLine} L${x(points.length - 1)},${PAD.t + ih} L${x(0)},${PAD.t + ih} Z`;
		const last = points[points.length - 1];
		return { x, y, valueLine, investedLine: path('invested'), area, max, min, last, iw, ih };
	});

	const gain = $derived(geom ? geom.last.value - (geom.last.invested ?? 0) : 0);

	// ── Hover tracking ──────────────────────────────────────────────────────────
	let hoverIdx = $state(null);
	const hp = $derived(hoverIdx != null && geom ? points[hoverIdx] : null);
	const hpGain = $derived(hp ? hp.value - (hp.invested ?? 0) : 0);
	// Tooltip horizontal position as a % of the plot (svg scales to container width).
	const tipLeft = $derived(geom && hoverIdx != null ? (geom.x(hoverIdx) / W) * 100 : 0);
	const tipAlign = $derived(tipLeft < 20 ? 'start' : tipLeft > 80 ? 'end' : 'center');

	function onMove(e) {
		if (!geom) return;
		const rect = e.currentTarget.getBoundingClientRect();
		const svgX = ((e.clientX - rect.left) / rect.width) * W;
		let i = Math.round(((svgX - PAD.l) / geom.iw) * (points.length - 1));
		hoverIdx = Math.max(0, Math.min(points.length - 1, i));
	}
	function onLeave() {
		hoverIdx = null;
	}
</script>

{#if geom}
	<div class="legend">
		<span class="lg"><i class="sw val"></i> Value</span>
		<span class="lg"><i class="sw inv"></i> Invested</span>
	</div>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="chartwrap"
		role="img"
		aria-label="invested vs portfolio value over time"
		onpointermove={onMove}
		onpointerleave={onLeave}
	>
		<svg viewBox="0 0 {W} {H}" class="vc">
			<defs>
				<linearGradient id="vcfill" x1="0" y1="0" x2="0" y2="1">
					<stop offset="0" stop-color="#b8860b" stop-opacity="0.22" />
					<stop offset="1" stop-color="#b8860b" stop-opacity="0.03" />
				</linearGradient>
			</defs>
			<g stroke="#fbe497" stroke-width="1">
				<line x1={PAD.l} y1={PAD.t} x2={W - PAD.r} y2={PAD.t} />
				<line x1={PAD.l} y1={(PAD.t + H - PAD.b) / 2} x2={W - PAD.r} y2={(PAD.t + H - PAD.b) / 2} />
				<line x1={PAD.l} y1={H - PAD.b} x2={W - PAD.r} y2={H - PAD.b} />
			</g>
			<path d={geom.area} fill="url(#vcfill)" />
			<!-- invested: dashed, muted -->
			<path
				d={geom.investedLine}
				fill="none"
				stroke="#a8224a"
				stroke-width="2"
				stroke-dasharray="5 4"
			/>
			<!-- value: solid ink -->
			<path d={geom.valueLine} fill="none" stroke="#96690a" stroke-width="2.5" />

			{#if hoverIdx != null && hp}
				<!-- guide line + markers at the hovered month -->
				<line
					x1={geom.x(hoverIdx)}
					y1={PAD.t}
					x2={geom.x(hoverIdx)}
					y2={H - PAD.b}
					stroke="#96690a"
					stroke-width="1"
					stroke-dasharray="3 3"
					opacity="0.5"
				/>
				<circle
					cx={geom.x(hoverIdx)}
					cy={geom.y(hp.invested ?? 0)}
					r="4"
					fill="#a8224a"
					stroke="#fefdf4"
					stroke-width="1.5"
				/>
				<circle
					cx={geom.x(hoverIdx)}
					cy={geom.y(hp.value)}
					r="4.5"
					fill="#96690a"
					stroke="#fefdf4"
					stroke-width="1.5"
				/>
			{:else}
				<circle cx={geom.x(points.length - 1)} cy={geom.y(geom.last.value)} r="4.5" fill="#96690a" />
				<circle
					cx={geom.x(points.length - 1)}
					cy={geom.y(geom.last.invested ?? 0)}
					r="3.5"
					fill="#a8224a"
				/>
			{/if}

			<g font-family="Inter, sans-serif" font-size="11" fill="#d4a017">
				<text x={PAD.l} y={H - 8}>{fmtDate(points[0].date)}</text>
				<text x={W - PAD.r} y={H - 8} text-anchor="end">{fmtDate(geom.last.date)}</text>
			</g>
		</svg>

		{#if hp}
			<div class="tip" style="left:{tipLeft}%" data-align={tipAlign}>
				<div class="tipdate">{fmtDate(hp.date)}</div>
				<div class="tiprow">
					<span class="k"><i class="sw val"></i> Value</span>
					<span class="v">{inr(hp.value)}</span>
				</div>
				<div class="tiprow">
					<span class="k"><i class="sw inv"></i> Invested</span>
					<span class="v">{inr(hp.invested ?? 0)}</span>
				</div>
				<div class="tiprow gainrow">
					<span class="k">Gain</span>
					<span class="v" class:up={hpGain >= 0} class:down={hpGain < 0}
						>{hpGain >= 0 ? '+' : ''}{inr(hpGain)}</span
					>
				</div>
			</div>
		{/if}
	</div>
	<p class="mini" style="margin:10px 0 0">
		Value <b>{inr(geom.last.value)}</b> · invested {inr(geom.last.invested)} ·
		<span class:up={gain >= 0} class:down={gain < 0}>
			{gain >= 0 ? '+' : ''}{inr(gain)} {gain >= 0 ? 'gain' : 'loss'}
		</span>
	</p>
{:else}
	<p class="dim">Not enough history to chart.</p>
{/if}

<style>
	.chartwrap {
		position: relative;
	}
	.vc {
		width: 100%;
		height: auto;
		display: block;
	}
	.legend {
		display: flex;
		gap: 16px;
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
		border-top-color: #96690a;
	}
	.sw.inv {
		border-top-color: #a8224a;
		border-top-style: dashed;
	}
	.up {
		color: var(--inflow);
		font-weight: 700;
	}
	.down {
		color: var(--outflow);
		font-weight: 700;
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
	}
	.tiprow .v {
		font-variant-numeric: tabular-nums;
		font-weight: 600;
		color: var(--ink);
		white-space: nowrap;
	}
	.tiprow .k {
		white-space: nowrap;
	}
	.tiprow.gainrow {
		margin-top: 4px;
		padding-top: 6px;
		border-top: 1px solid var(--primary-300);
	}
</style>
