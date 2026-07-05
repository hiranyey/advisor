<script>
	import { fmtDate } from '$lib/api.js';
	import { renderRichHtml } from '$lib/richtext.js';
	import { Sparkles, RefreshCw } from '@lucide/svelte';

	// insights: BookInsightsResponse | null. onRefresh: () => Promise<void>
	let { insights = null, onRefresh } = $props();

	let refreshing = $state(false);

	async function refresh() {
		if (refreshing) return;
		refreshing = true;
		try {
			await onRefresh?.();
		} finally {
			refreshing = false;
		}
	}

	// renderRichHtml escapes all plain-text segments and only ever emits the fixed
	// <span class="rt-*"> shape for {{tag:span}} matches, so this is safe for @html
	// even though the source string comes from the LLM.
	const headlineHtml = $derived(renderRichHtml(insights?.headline));
	const briefingHtml = $derived(renderRichHtml(insights?.briefing));
</script>

<div class="briefing">
	<div class="bhead">
		<span class="bicon"><Sparkles size={16} strokeWidth={2} /></span>
		<span class="btitle">Morning briefing</span>
		{#if insights?.as_of_date}
			<span class="dim basof">as of {fmtDate(insights.as_of_date)}</span>
		{/if}
		<button class="refresh" onclick={refresh} disabled={refreshing || !insights?.llm_configured}>
			<RefreshCw size={12} strokeWidth={2.2} class={refreshing ? 'spin' : ''} />
			{refreshing ? 'Thinking…' : 'Regenerate'}
		</button>
	</div>

	{#if !insights?.llm_configured}
		<p class="dim">AI briefing unavailable — the backend has no LLM key configured.</p>
	{:else if insights.briefing}
		{#if insights.headline}
			<p class="bheadline">{@html headlineHtml}</p>
		{/if}
		<p class="btext">{@html briefingHtml}</p>
	{:else}
		<p class="dim">No briefing yet — click Regenerate to have the AI read today's book.</p>
	{/if}
</div>

<style>
	.briefing {
		border: 1.5px solid var(--primary-800);
		background: var(--card);
		box-shadow: var(--shadow-stamp-sm);
		padding: 16px 18px;
	}
	.bhead {
		display: flex;
		align-items: center;
		gap: 9px;
		margin-bottom: 8px;
		flex-wrap: wrap;
	}
	.bicon {
		color: var(--brand);
		display: flex;
	}
	.btitle {
		font-family: var(--font-sans);
		font-size: 11px;
		font-weight: 700;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--brand-strong);
	}
	.basof {
		font-size: 12px;
	}
	.refresh {
		margin-left: auto;
		display: inline-flex;
		align-items: center;
		gap: 5px;
		font-family: var(--font-sans);
		font-size: 10.5px;
		font-weight: 700;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--mut);
		background: var(--card-2);
		border: 1px solid var(--primary-300);
		padding: 5px 10px;
		cursor: pointer;
		transition:
			color 140ms ease,
			border-color 140ms ease;
	}
	.refresh:hover:not(:disabled) {
		color: var(--brand-strong);
		border-color: var(--primary-800);
	}
	.refresh:disabled {
		opacity: 0.55;
		cursor: default;
	}
	.refresh :global(.spin) {
		animation: spin 900ms linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	.bheadline {
		font-family: var(--font-serif);
		font-style: italic;
		font-weight: 700;
		font-size: 34px;
		line-height: 1.22;
		color: var(--ink);
		margin-bottom: 10px;
		letter-spacing: -0.01em;
	}
	.btext {
		font-family: var(--font-serif);
		font-size: 16px;
		line-height: 1.55;
		color: var(--ink-2);
	}

	/* Inline emphasis tokens from the LLM's {{tag:span}} markup, injected via {@html} —
	   :global() because Svelte can't see selectors that only exist inside raw HTML.
	   Real typographic variety (size/weight/color/style/marks), not one flat color. */
	.bheadline :global(.rt-hero) {
		font-weight: 800;
		font-style: normal;
		font-size: 1.5em;
		color: var(--brand-strong);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
		background: linear-gradient(180deg, transparent 62%, rgba(212, 160, 23, 0.32) 62%);
		padding: 0 2px;
	}
	.btext :global(.rt-hero) {
		font-weight: 800;
		font-style: normal;
		font-size: 1.2em;
		color: var(--brand-strong);
		font-variant-numeric: tabular-nums;
	}
	:global(.rt-good) {
		font-weight: 700;
		font-style: normal;
		color: var(--inflow);
	}
	:global(.rt-warn) {
		font-weight: 700;
		font-style: normal;
		color: var(--primary-800);
	}
	:global(.rt-bad) {
		font-weight: 700;
		font-style: normal;
		color: var(--outflow);
	}
	:global(.rt-muted) {
		font-family: var(--font-sans);
		font-style: normal;
		font-weight: 600;
		font-size: 0.62em;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--faint);
		vertical-align: 2px;
	}
	@media (prefers-reduced-motion: reduce) {
		.refresh :global(.spin) {
			animation: none;
		}
	}
</style>
