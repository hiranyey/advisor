<script>
	import { page } from '$app/state';
	import { api, fmtDate } from '$lib/api.js';
	import AnswerBody from '$lib/components/AnswerBody.svelte';
	import ToolResult from '$lib/components/ToolResult.svelte';
	import { Sparkles, FileWarning } from '@lucide/svelte';

	const token = $derived(page.params.token);

	let debrief = $state(null);
	let loading = $state(true);
	let notFound = $state(false);

	$effect(() => {
		load(token);
	});

	async function load(t) {
		loading = true;
		notFound = false;
		debrief = null;
		try {
			debrief = await api.getDebrief(t);
		} catch (e) {
			notFound = true;
		} finally {
			loading = false;
		}
	}
</script>

<svelte:head><title>{debrief ? `${debrief.client_name} — report` : 'Report'}</title></svelte:head>

<div class="reportpage">
	{#if loading}
		<div class="loading">Loading report…</div>
	{:else if notFound}
		<div class="notfound">
			<FileWarning size={28} strokeWidth={1.6} />
			<h1>This report link isn't valid</h1>
			<p>It may have been mistyped, or the report no longer exists.</p>
		</div>
	{:else if debrief}
		<header class="rhead">
			<span class="brand"><Sparkles size={15} strokeWidth={2} /> AdvisorOS</span>
			<h1>{debrief.client_name}</h1>
			<p class="question">{debrief.question}</p>
			<p class="meta">Prepared {fmtDate(debrief.created_at)}</p>
		</header>

		<div class="rbody">
			<AnswerBody text={debrief.answer} clients={[]} />
			{#each debrief.trace as entry, i}
				<ToolResult {entry} index={i} />
			{/each}
		</div>

		<footer class="rfoot">This report was generated for informational purposes and isn't investment advice.</footer>
	{/if}
</div>

<style>
	.reportpage {
		max-width: 760px;
		margin: 0 auto;
		padding: 48px 24px 80px;
	}
	.loading {
		text-align: center;
		padding: 80px 0;
		color: var(--mut);
		font-family: var(--font-sans);
	}
	.notfound {
		display: flex;
		flex-direction: column;
		align-items: center;
		text-align: center;
		gap: 8px;
		padding: 100px 0;
		color: var(--mut);
	}
	.notfound h1 {
		font-family: var(--font-serif);
		font-size: 22px;
		color: var(--ink);
		margin-top: 6px;
	}
	.rhead {
		text-align: center;
		margin-bottom: 36px;
		padding-bottom: 28px;
		border-bottom: 2px solid var(--primary-800);
	}
	.brand {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-family: var(--font-sans);
		font-size: 11px;
		font-weight: 700;
		letter-spacing: 0.16em;
		text-transform: uppercase;
		color: var(--brand-strong);
		margin-bottom: 18px;
	}
	.rhead h1 {
		font-family: var(--font-serif);
		font-weight: 800;
		font-size: clamp(28px, 5vw, 44px);
		color: var(--ink);
		margin-bottom: 10px;
	}
	.question {
		font-family: var(--font-serif);
		font-style: italic;
		font-size: 18px;
		color: var(--ink-2);
		max-width: 560px;
		margin: 0 auto 10px;
	}
	.meta {
		font-size: 12.5px;
		color: var(--mut);
	}
	.rbody {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.rfoot {
		text-align: center;
		font-size: 12px;
		color: var(--faint, var(--mut));
		margin-top: 40px;
	}
</style>
