<script>
	import { inr, pct, catLabel, catColor } from '$lib/api.js';
	import { catIcon } from '$lib/icons.js';
	import { ArrowUp, ArrowDown } from '@lucide/svelte';

	// allocation: [{ category, value, weight }]
	// deltas: { [category]: weight_change }  — change in share since the first scored day
	//   (a fraction; ×100 = percentage points). Optional; chips hidden when absent.
	let { allocation = [], deltas = {} } = $props();

	// Shared hover state links the bar segment and its list row, both directions.
	let hovered = $state(null);

	// Only surface a chip when the share moved a visible amount (≥0.1pp).
	function delta(cat) {
		const d = deltas?.[cat];
		return d != null && Math.abs(d) >= 0.001 ? d : null;
	}
</script>

<div class="allocbar" class:dimmed={hovered !== null} role="img" aria-label="category allocation">
	{#each allocation as a}
		<div
			class="seg"
			class:hot={hovered === a.category}
			style="width:{a.weight * 100}%; background:{catColor(a.category)}"
			title="{catLabel(a.category)} · {pct(a.weight, 1)}"
			onmouseenter={() => (hovered = a.category)}
			onmouseleave={() => (hovered = null)}
		></div>
	{/each}
</div>

<div class="alloclist">
	{#each allocation as a}
		{@const Icon = catIcon(a.category)}
		<div
			class="allocrow"
			class:hot={hovered === a.category}
			class:dim={hovered !== null && hovered !== a.category}
			style="--cat:{catColor(a.category)}"
			onmouseenter={() => (hovered = a.category)}
			onmouseleave={() => (hovered = null)}
		>
			<span class="ci" style="color:{catColor(a.category)}"><Icon size={15} strokeWidth={1.9} /></span>
			<span class="cn">{catLabel(a.category)}</span>
			{#if delta(a.category) != null}
				{@const d = delta(a.category)}
				<span class="cd num" class:up={d > 0} class:down={d < 0} title="share change vs a month ago">
					{#if d > 0}<ArrowUp size={11} strokeWidth={2.6} />{:else}<ArrowDown size={11} strokeWidth={2.6} />{/if}
					{Math.abs(d * 100).toFixed(1)}%
				</span>
			{/if}
			<span class="cv num">{inr(a.value)}</span>
			<span class="cp num">{pct(a.weight, 1)}</span>
		</div>
	{/each}
</div>

<style>
	.alloclist {
		margin-top: 8px;
	}

	/* Let a hovered segment pop above the bar's edges. */
	.allocbar {
		overflow: visible;
		margin: 4px 0;
	}
	.seg {
		position: relative;
		transform-origin: center;
		transition:
			transform 170ms var(--ease-out, ease),
			opacity 170ms ease,
			filter 170ms ease;
	}
	.allocbar.dimmed .seg {
		opacity: 0.35;
	}
	.seg.hot {
		opacity: 1;
		transform: scaleY(1.7);
		z-index: 2;
		filter: brightness(1.06) saturate(1.25);
	}

	/* List rows: the hovered category turns vibrant, the rest recede. */
	.allocrow {
		border-left: 3px solid transparent;
		padding-left: 9px;
		transition:
			background 150ms ease,
			color 150ms ease,
			border-color 150ms ease,
			opacity 150ms ease;
		cursor: default;
	}
	.allocrow.hot {
		background: color-mix(in srgb, var(--cat) 15%, transparent);
		border-left-color: var(--cat);
	}
	.allocrow.hot .cn {
		color: var(--ink);
		font-weight: 700;
	}
	.allocrow.hot .cp {
		color: var(--cat);
	}
	.allocrow.dim {
		opacity: 0.4;
	}
	.allocrow .ci {
		transition: transform 150ms var(--ease-out, ease);
	}
	.allocrow.hot .ci {
		transform: scale(1.25);
	}

	/* Share-change chip: how this category's slice moved since the first scored run. */
	.cd {
		display: inline-flex;
		align-items: center;
		gap: 2px;
		font-size: 11px;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		padding: 1px 5px 1px 3px;
		border-radius: 3px;
		white-space: nowrap;
	}
	.cd.up {
		color: var(--inflow);
		background: color-mix(in srgb, var(--inflow) 12%, transparent);
	}
	.cd.down {
		color: var(--outflow);
		background: color-mix(in srgb, var(--outflow) 12%, transparent);
	}

	@media (prefers-reduced-motion: reduce) {
		.seg,
		.allocrow,
		.allocrow .ci {
			transition: none;
		}
		.seg.hot {
			transform: none;
		}
	}
</style>
