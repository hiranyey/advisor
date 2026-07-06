<script>
	import { fmtDate, inr, pct, catLabel, catColor } from '$lib/api.js';

	// points: [{as_of_date, aum, allocation:[{category,value,weight}]}] oldest→newest.
	// Every point carries the same category set in the same order (backend guarantee),
	// so we can stack absolute ₹ values into areas keyed by category index.
	let { points = [] } = $props();

	const W = 640;
	const H = 200;
	const PAD = { l: 8, r: 8, t: 10, b: 24 };

	// Category order + colors are taken from the first point (identical across all points).
	const cats = $derived(points[0]?.allocation.map((a) => a.category) ?? []);

	const geom = $derived.by(() => {
		if (points.length < 2 || !cats.length) return null;
		const max = Math.max(1, ...points.map((p) => p.aum));
		const iw = W - PAD.l - PAD.r;
		const ih = H - PAD.t - PAD.b;
		const x = (i) => PAD.l + (i / (points.length - 1)) * iw;
		const y = (v) => PAD.t + ih - (v / max) * ih;

		// Cumulative stack per point: running total up to and including each category.
		const stacks = points.map((p) => {
			let acc = 0;
			return p.allocation.map((a) => (acc += a.value));
		});

		const areas = cats.map((_, bi) => {
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
	// Hovered day's biggest slices — what the tooltip actually lists.
	const hpTop = $derived(
		hp ? [...hp.allocation].filter((a) => a.value > 0).sort((a, b) => b.value - a.value).slice(0, 6) : []
	);
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
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="chartwrap"
		role="img"
		aria-label="book allocation by category over time"
		onpointermove={onMove}
		onpointerleave={onLeave}
	>
		<svg viewBox="0 0 {W} {H}" class="tc">
			<g stroke="#fbe497" stroke-width="1">
				<line x1={PAD.l} y1={PAD.t} x2={W - PAD.r} y2={PAD.t} />
				<line x1={PAD.l} y1={H - PAD.b} x2={W - PAD.r} y2={H - PAD.b} />
			</g>
			{#each geom.areas as d, i}
				<path {d} fill={catColor(cats[i])} fill-opacity="0.82" stroke="#fefdf4" stroke-width="0.4" />
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
				<div class="tipdate">{fmtDate(hp.as_of_date)} · {inr(hp.aum)}</div>
				{#each hpTop as a}
					<div class="tiprow">
						<span class="k"><i class="sw" style="background:{catColor(a.category)}"></i>{catLabel(a.category)}</span>
						<span class="v">{pct(a.weight, 0)}</span>
					</div>
				{/each}
			</div>
		{/if}
	</div>
{:else}
	<p class="dim single-note">
		One scored run so far — the allocation trend fills in as the book analysis re-runs.
	</p>
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
		min-width: 170px;
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
	.single-note {
		margin: 4px 0;
	}
</style>
