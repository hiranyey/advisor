<script>
	import { fmtDate } from '$lib/api.js';
	import { ArrowUpRight, ArrowDownRight } from '@lucide/svelte';

	// points: [{as_of_date, breach, watch, ok, aum}] oldest→newest. movers: [{client_id,name,direction,from_status,to_status}]
	let { points = [], movers = [] } = $props();

	const W = 640;
	const H = 200;
	const PAD = { l: 8, r: 8, t: 10, b: 24 };

	// Stacked-area bands: ok (bottom) → watch → breach (top), so the "bad" edge is the
	// silhouette that reads first. Reuses the app's fixed status triad everywhere else
	// (RiskPill / heatmap): good=inflow green, watch=gold, breach=outflow rose.
	const BANDS = [
		{ key: 'ok', label: 'Fine', color: '#4a7c3a' },
		{ key: 'watch', label: 'At limit', color: '#b8860b' },
		{ key: 'breach', label: 'Too risky', color: '#a8224a' }
	];

	const geom = $derived.by(() => {
		if (points.length < 2) return null;
		const totals = points.map((p) => p.breach + p.watch + p.ok);
		const max = Math.max(1, ...totals);
		const iw = W - PAD.l - PAD.r;
		const ih = H - PAD.t - PAD.b;
		const x = (i) => PAD.l + (points.length === 1 ? 0 : (i / (points.length - 1)) * iw);
		const y = (v) => PAD.t + ih - (v / max) * ih;

		// Cumulative stack per point: [ok, ok+watch, ok+watch+breach].
		const stacks = points.map((p) => {
			let acc = 0;
			return BANDS.map((b) => (acc += p[b.key]));
		});

		const areas = BANDS.map((b, bi) => {
			const top = points.map((_, i) => `${i === 0 ? 'M' : 'L'}${x(i)},${y(stacks[i][bi])}`).join(' ');
			const bottomBase = bi === 0 ? points.map(() => 0) : stacks.map((s) => s[bi - 1]);
			const bottom = points
				.map((_, i) => i)
				.reverse()
				.map((i) => `L${x(i)},${y(bottomBase[i])}`)
				.join(' ');
			return `${top} ${bottom} Z`;
		});

		return { x, y, iw, ih, max, stacks, areas };
	});

	let hoverIdx = $state(null);
	const hp = $derived(hoverIdx != null ? points[hoverIdx] : null);
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

	const DIR_ICON = { worsened: ArrowUpRight, improved: ArrowDownRight };
</script>

{#if geom}
	<div class="legend">
		{#each BANDS as b}
			<span class="lg"><i class="sw" style="background:{b.color}"></i>{b.label}</span>
		{/each}
	</div>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="chartwrap"
		role="img"
		aria-label="client risk status counts over time"
		onpointermove={onMove}
		onpointerleave={onLeave}
	>
		<svg viewBox="0 0 {W} {H}" class="tc">
			<g stroke="#fbe497" stroke-width="1">
				<line x1={PAD.l} y1={PAD.t} x2={W - PAD.r} y2={PAD.t} />
				<line x1={PAD.l} y1={H - PAD.b} x2={W - PAD.r} y2={H - PAD.b} />
			</g>
			{#each geom.areas as d, i}
				<path {d} fill={BANDS[i].color} fill-opacity="0.75" />
			{/each}
			{#if hoverIdx != null}
				<line
					x1={geom.x(hoverIdx)}
					y1={PAD.t}
					x2={geom.x(hoverIdx)}
					y2={H - PAD.b}
					stroke="#fefdf4"
					stroke-width="1.5"
					opacity="0.8"
				/>
			{/if}
			<g font-family="Inter, sans-serif" font-size="11" fill="#96690a">
				<text x={PAD.l} y={H - 8}>{fmtDate(points[0].as_of_date)}</text>
				<text x={W - PAD.r} y={H - 8} text-anchor="end"
					>{fmtDate(points[points.length - 1].as_of_date)}</text
				>
			</g>
		</svg>

		{#if hp}
			<div class="tip" style="left:{tipLeft}%" data-align={tipAlign}>
				<div class="tipdate">{fmtDate(hp.as_of_date)}</div>
				{#each BANDS as b}
					<div class="tiprow">
						<span class="k"><i class="sw" style="background:{b.color}"></i>{b.label}</span>
						<span class="v">{hp[b.key]}</span>
					</div>
				{/each}
			</div>
		{/if}
	</div>

	{#if movers.length}
		<div class="movers">
			<p class="moverlabel">Since last run</p>
			{#each movers as m}
				{@const Icon = DIR_ICON[m.direction]}
				<span class="mover {m.direction}">
					<Icon size={12} strokeWidth={2.4} />
					{m.name} · {m.from_status} → {m.to_status}
				</span>
			{/each}
		</div>
	{/if}
{:else}
	<div class="single">
		{#each BANDS as b}
			<div class="scount" style="--c:{b.color}">
				<span class="sval num">{points[0]?.[b.key] ?? 0}</span>
				<span class="slabel">{b.label}</span>
			</div>
		{/each}
		<p class="dim single-note">
			One scored run so far — the trend fills in as the book analysis re-runs.
		</p>
	</div>
{/if}

<style>
	.chartwrap {
		position: relative;
	}
	.tc {
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
		font-weight: 600;
	}
	.lg {
		display: inline-flex;
		align-items: center;
		gap: 6px;
	}
	.sw {
		width: 10px;
		height: 10px;
		display: inline-block;
		border-radius: 2px;
		flex: none;
	}

	.tip {
		position: absolute;
		top: 4px;
		pointer-events: none;
		background: var(--paper);
		border: 1.5px solid var(--primary-800);
		box-shadow: var(--shadow-stamp-sm);
		padding: 8px 10px;
		min-width: 140px;
		z-index: 3;
	}
	.tip[data-align='center'] {
		transform: translateX(-50%);
	}
	.tip[data-align='end'] {
		transform: translateX(-100%);
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
		font-weight: 700;
		color: var(--ink);
	}

	.movers {
		margin-top: 14px;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		padding-top: 12px;
		border-top: 1px dashed var(--rule);
	}
	.moverlabel {
		font-family: var(--font-sans);
		font-size: 10.5px;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--faint);
		margin-right: 4px;
	}
	.mover {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		font-size: 12px;
		font-weight: 600;
		padding: 3px 8px;
		border: 1px solid var(--rule);
		background: var(--card);
	}
	.mover.worsened {
		color: var(--outflow);
	}
	.mover.improved {
		color: var(--inflow);
	}

	.single {
		display: flex;
		align-items: baseline;
		gap: 28px;
		flex-wrap: wrap;
	}
	.scount {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.scount .sval {
		font-family: var(--font-serif);
		font-weight: 800;
		font-size: 26px;
		color: var(--c);
	}
	.scount .slabel {
		font-family: var(--font-sans);
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		font-weight: 600;
		color: var(--mut);
	}
	.single-note {
		flex-basis: 100%;
		margin-top: 4px;
	}
</style>
