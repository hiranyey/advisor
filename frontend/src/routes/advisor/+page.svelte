<script>
	// Placeholder — the Copilot (six-tool LLM loop) is a later phase. This page
	// holds the tab and previews the interaction so the shell is complete.
	import { Sparkles, MessageSquare } from '@lucide/svelte';

	const chips = [
		'Who should I call first, and why?',
		'If small-cap fell 20% over 3 months, who couldn’t stomach it?',
		'Prep me for my 3pm with Rahul',
		'₹50k into HDFC Small Cap for client 12 last month'
	];
</script>

<div class="page">
	<p class="eyebrow">Copilot Workspace</p>
	<div class="titlewrap">
		<span class="titleicon"><Sparkles size={44} strokeWidth={1.5} /></span>
		<h1 class="title">AI Advisor</h1>
	</div>
	<div class="titlerule"></div>

	<div class="card full">
		<h2>Coming online next</h2>
		<p class="h2sub">
			The Copilot is a fixed six-tool set — query the book, pull a client brief, run a live
			what-if, stress the whole book, rank the call list, and log transactions from plain English.
			It reads the same APIs the other tabs render, then narrates the result.
		</p>
		<div class="h2rule"></div>
		<div class="chips">
			{#each chips as c}
				<span class="chip"><MessageSquare size={14} strokeWidth={1.8} />{c}</span>
			{/each}
		</div>
		<p class="mini" style="margin-top:18px">
			Wired once the LLM + Monte Carlo engine land. Until then, the <a href="/">Risk Radar</a> and
			<a href="/clients">Clients</a> tabs run on live data.
		</p>
	</div>
</div>

<style>
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 10px;
	}
	.chip {
		font-family: var(--font-sans);
		font-size: 13px;
		padding: 9px 14px;
		background: var(--card-2);
		border: 1.5px solid var(--primary-800);
		box-shadow: var(--shadow-stamp-sm);
		color: var(--ink-2);
		display: inline-flex;
		align-items: center;
		gap: 8px;
	}
	.chip :global(svg) {
		color: var(--brand);
		flex: none;
	}
</style>
