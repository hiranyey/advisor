<script>
	import { inr, pct, catLabel, catColor } from '$lib/api.js';
	import { catIcon } from '$lib/icons.js';

	// allocation: [{ category, value, weight }]
	let { allocation = [] } = $props();

	// Shared hover state links the bar segment and its list row, both directions.
	let hovered = $state(null);
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
