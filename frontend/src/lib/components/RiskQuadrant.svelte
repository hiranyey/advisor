<script>
	import { goto } from '$app/navigation';
	import { pct } from '$lib/api.js';

	// points: [{client_id, name, risk_profile, mismatch, worst_goal_prob, portfolio_value}]
	let { points = [], confidence = 0.8 } = $props();

	const W = 640;
	const H = 380;
	const PAD = { l: 42, r: 16, t: 16, b: 34 };

	// Same risk-profile → color mapping RiskPill already uses everywhere else in the app
	// (aggressive = crit/rose, balanced = warn/gold, conservative = good/green), so a
	// client's dot color always means the same thing it does on their own page.
	const PROFILE_COLOR = { conservative: '#4a7c3a', balanced: '#b8860b', aggressive: '#a8224a' };
	const PROFILE_ORDER = ['conservative', 'balanced', 'aggressive'];

	const plotted = $derived(points.filter((p) => p.worst_goal_prob != null));
	const noGoals = $derived(points.length - plotted.length);

	const geom = $derived.by(() => {
		if (!plotted.length) return null;
		const iw = W - PAD.l - PAD.r;
		const ih = H - PAD.t - PAD.b;
		const mismatches = plotted.map((p) => p.mismatch);
		const xMin = Math.min(-0.05, ...mismatches);
		const xMax = Math.max(0.05, ...mismatches);
		const xPad = (xMax - xMin) * 0.08 || 0.05;
		const x0 = xMin - xPad;
		const x1 = xMax + xPad;
		const x = (m) => PAD.l + ((m - x0) / (x1 - x0)) * iw;
		const y = (p) => PAD.t + ih - Math.max(0, Math.min(1, p)) * ih;

		const values = plotted.map((p) => p.portfolio_value);
		const vMax = Math.max(1, ...values);
		const vMin = Math.min(...values);
		const r = (v) => {
			const t = vMax > vMin ? (Math.sqrt(v) - Math.sqrt(vMin)) / (Math.sqrt(vMax) - Math.sqrt(vMin)) : 0.5;
			return 4 + t * 10;
		};

		return { x, y, r, x0, x1, zeroX: x(0), confY: y(confidence) };
	});

	let hovered = $state(null); // {p, cx, cy}
	const tipPos = $derived.by(() => {
		if (!hovered) return null;
		const left = (hovered.cx / W) * 100;
		const top = (hovered.cy / H) * 100;
		const tx = left < 25 ? '0' : left > 75 ? '-100%' : '-50%';
		const ty = top > 65 ? 'calc(-100% - 14px)' : '14px';
		return { left, top, transform: `translate(${tx}, ${ty})` };
	});
</script>

{#if geom}
	<div class="legend">
		{#each PROFILE_ORDER as p}
			<span class="lg"><i class="sw" style="background:{PROFILE_COLOR[p]}"></i>{p}</span>
		{/each}
		<span class="lg dim">· dot size = portfolio value</span>
	</div>

	<div class="chartwrap">
		<svg viewBox="0 0 {W} {H}" class="rq">
			<g stroke="#fbe497" stroke-width="1">
				<line x1={PAD.l} y1={PAD.t} x2={PAD.l} y2={H - PAD.b} />
				<line x1={PAD.l} y1={H - PAD.b} x2={W - PAD.r} y2={H - PAD.b} />
			</g>

			<!-- Quadrant guide lines: in-tolerance boundary + goal-confidence target -->
			<line x1={geom.zeroX} y1={PAD.t} x2={geom.zeroX} y2={H - PAD.b} stroke="#d4a017" stroke-width="1" />
			<line
				x1={PAD.l}
				y1={geom.confY}
				x2={W - PAD.r}
				y2={geom.confY}
				stroke="#d4a017"
				stroke-width="1"
			/>

			<g font-family="Inter, sans-serif" font-size="10.5" fill="#96690a" font-weight="600">
				<text x={geom.zeroX + 4} y={PAD.t + 11}>riskier than comfortable →</text>
				<text x={geom.zeroX - 4} y={PAD.t + 11} text-anchor="end">← within comfort zone</text>
				<text x={W - PAD.r} y={geom.confY - 5} text-anchor="end">{pct(confidence)} odds of hitting goals</text>
				<text x={PAD.l - 8} y={PAD.t + 4} text-anchor="end">1.0</text>
				<text x={PAD.l - 8} y={H - PAD.b} text-anchor="end">0</text>
			</g>

			<!-- Quadrant labels — muted ink, never the data color (dataviz: text stays in text tokens) -->
			<g font-family="Georgia, serif" font-size="13" fill="#96690a" opacity="0.55" font-style="italic">
				<text x={PAD.l + 10} y={PAD.t + 22}>On track</text>
				<text x={W - PAD.r - 10} y={PAD.t + 22} text-anchor="end">Too much risk</text>
				<text x={PAD.l + 10} y={H - PAD.b - 10}>Behind on goals</text>
				<text x={W - PAD.r - 10} y={H - PAD.b - 10} text-anchor="end">Needs attention</text>
			</g>

			{#each plotted as p}
				{@const cx = geom.x(p.mismatch)}
				{@const cy = geom.y(p.worst_goal_prob)}
				{@const rad = geom.r(p.portfolio_value)}
				<circle
					{cx}
					{cy}
					r={rad}
					fill={PROFILE_COLOR[p.risk_profile] ?? '#96690a'}
					fill-opacity={hovered?.p === p ? 0.95 : 0.65}
					stroke="#fefdf4"
					stroke-width="1.5"
				/>
				<!-- Hit target: at least 24px diameter, and the tooltip/click surface. -->
				<circle
					{cx}
					{cy}
					r={Math.max(12, rad)}
					fill="transparent"
					role="button"
					tabindex="0"
					aria-label="{p.name} — open client"
					onpointerenter={() => (hovered = { p, cx, cy })}
					onpointerleave={() => (hovered = null)}
					onclick={() => goto(`/clients/${p.client_id}`)}
					onkeydown={(e) => e.key === 'Enter' && goto(`/clients/${p.client_id}`)}
					style="cursor:pointer"
				/>
			{/each}
		</svg>

		{#if hovered && tipPos}
			<div class="tip" style="left:{tipPos.left}%; top:{tipPos.top}%; transform:{tipPos.transform}">
				<div class="tipname">{hovered.p.name}</div>
				<div class="tiprow">
					<span class="k">Risk profile</span><span class="v">{hovered.p.risk_profile}</span>
				</div>
				<div class="tiprow">
					<span class="k">Vs. tolerance</span>
					<span class="v" class:bad={hovered.p.mismatch > 0}
						>{hovered.p.mismatch > 0 ? '+' : ''}{(hovered.p.mismatch * 100).toFixed(1)}%</span
					>
				</div>
				<div class="tiprow">
					<span class="k">Chance of hitting weakest goal</span>
					<span class="v" class:bad={hovered.p.worst_goal_prob < confidence}
						>{pct(hovered.p.worst_goal_prob)}</span
					>
				</div>
			</div>
		{/if}
	</div>
	{#if noGoals}
		<p class="dim note">{noGoals} client{noGoals === 1 ? '' : 's'} not shown — no goals recorded.</p>
	{/if}
{:else}
	<p class="dim">Not enough scored clients yet.</p>
{/if}

<style>
	.chartwrap {
		position: relative;
	}
	.rq {
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
		text-transform: capitalize;
		align-items: center;
	}
	.legend .dim {
		text-transform: none;
		font-weight: 400;
		color: var(--faint);
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
		border-radius: 50%;
		flex: none;
	}
	.note {
		margin-top: 8px;
		font-size: 12px;
	}

	.tip {
		position: absolute;
		pointer-events: none;
		background: var(--paper);
		border: 1.5px solid var(--primary-800);
		box-shadow: var(--shadow-stamp-sm);
		padding: 8px 10px;
		min-width: 160px;
		z-index: 3;
	}
	.tipname {
		font-family: var(--font-serif);
		font-weight: 700;
		font-size: 14px;
		color: var(--ink);
		margin-bottom: 5px;
	}
	.tiprow {
		display: flex;
		justify-content: space-between;
		gap: 16px;
		font-size: 12.5px;
		padding: 2px 0;
		text-transform: capitalize;
	}
	.tiprow .k {
		color: var(--ink-2);
	}
	.tiprow .v {
		font-variant-numeric: tabular-nums;
		font-weight: 700;
		color: var(--ink);
	}
	.tiprow .v.bad {
		color: var(--outflow);
	}
</style>
