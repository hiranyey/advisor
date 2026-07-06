<script>
	// Share panel: pick (or write) a client-specific question, watch it generate live
	// (same job+SSE shape as the Copilot — see debriefJobEventsUrl), then get back a
	// public, no-login link to a standalone one-pager report for that question.
	import { api, debriefJobEventsUrl, fmtDate } from '$lib/api.js';
	import {
		X,
		Sparkles,
		Send,
		Copy,
		Check,
		ExternalLink,
		LoaderCircle,
		FileText
	} from '@lucide/svelte';

	let { clientId, onClose } = $props();

	let suggestions = $state(null);
	let suggestionsError = $state(null);
	let pastDebriefs = $state([]);
	let customQuestion = $state('');

	// idle (pick a question) | running (job in flight) | done (share_token ready) | error
	let jobStatus = $state('idle');
	let activeQuestion = $state('');
	let activeJobId = $state(null);
	let progress = $state([]);
	let resultToken = $state(null);
	let jobError = $state(null);
	let copied = $state(false);

	const TOOL_LABELS = {
		get_client_brief: 'Pulling the latest client analysis…',
		run_whatif: 'Re-simulating the what-if…',
		project_portfolio: 'Projecting the portfolio forward…',
		stress_book: 'Stress-testing against the book…',
		rank_goal_shortfalls: 'Checking goal shortfalls…',
		query_book: 'Looking up client data…'
	};

	async function load() {
		try {
			const [s, d] = await Promise.all([
				api.getDebriefSuggestions(clientId),
				api.listDebriefs(clientId)
			]);
			suggestions = s.suggestions;
			pastDebriefs = d;
		} catch (e) {
			suggestionsError = e.message;
		}
	}
	load();

	function shareUrl(token) {
		return `${window.location.origin}/share/${token}`;
	}

	async function pickQuestion(question) {
		question = question.trim();
		if (!question) return;
		jobStatus = 'running';
		activeQuestion = question;
		progress = [];
		jobError = null;
		resultToken = null;
		try {
			const { job_id } = await api.startDebriefJob(clientId, question);
			activeJobId = job_id;
			streamJob(job_id);
		} catch (e) {
			jobStatus = 'error';
			jobError = e.message;
		}
	}

	function streamJob(jobId) {
		const es = new EventSource(debriefJobEventsUrl(jobId));
		es.onmessage = (ev) => {
			let data;
			try {
				data = JSON.parse(ev.data);
			} catch {
				return;
			}
			if (data.type === 'tool_call') {
				progress.push(TOOL_LABELS[data.tool] ?? `Running ${data.tool}…`);
			} else if (data.type === 'final') {
				jobStatus = 'done';
				resultToken = data.share_token;
				es.close();
			} else if (data.type === 'error') {
				jobStatus = 'error';
				jobError = data.detail;
				es.close();
			} else if (data.type === 'cancelled') {
				jobStatus = 'idle';
				es.close();
			}
		};
		es.onerror = () => {
			if (jobStatus === 'running') {
				jobStatus = 'error';
				jobError = 'Lost connection while generating the report.';
			}
			es.close();
		};
	}

	function reset() {
		jobStatus = 'idle';
		activeQuestion = '';
		activeJobId = null;
		progress = [];
		jobError = null;
		resultToken = null;
		copied = false;
		load(); // pick up the newly created report in "past reports"
	}

	async function copyLink() {
		await navigator.clipboard.writeText(shareUrl(resultToken));
		copied = true;
		setTimeout(() => (copied = false), 1800);
	}

	function openReport(token) {
		window.open(`/share/${token}`, '_blank');
	}

	function onKeydown(e) {
		if (e.key === 'Escape') onClose();
	}
</script>

<svelte:window onkeydown={onKeydown} />

<div class="backdrop" onclick={onClose}>
	<div class="panel" onclick={(e) => e.stopPropagation()}>
		<div class="phead">
			<span class="picon"><Sparkles size={17} strokeWidth={2} /></span>
			<h2>Share a report</h2>
			<button class="close" onclick={onClose} aria-label="Close"><X size={16} strokeWidth={2} /></button>
		</div>

		{#if jobStatus === 'running'}
			<div class="state">
				<p class="qline">"{activeQuestion}"</p>
				<div class="spin"><LoaderCircle size={16} strokeWidth={2} class="spinicon" /> Generating the report…</div>
				<ul class="progress">
					{#each progress as p}<li>{p}</li>{/each}
				</ul>
			</div>
		{:else if jobStatus === 'done'}
			<div class="state">
				<p class="qline">"{activeQuestion}"</p>
				<p class="ready"><Check size={15} strokeWidth={2.4} /> Report ready</p>
				<div class="linkrow">
					<input readonly value={shareUrl(resultToken)} onclick={(e) => e.target.select()} />
					<button class="iconbtn" onclick={copyLink} title="Copy link">
						{#if copied}<Check size={14} strokeWidth={2.2} />{:else}<Copy size={14} strokeWidth={2} />{/if}
					</button>
					<button class="iconbtn" onclick={() => openReport(resultToken)} title="Open report">
						<ExternalLink size={14} strokeWidth={2} />
					</button>
				</div>
				<button class="linkbtn" onclick={reset}>Share another question</button>
			</div>
		{:else if jobStatus === 'error'}
			<div class="state">
				<p class="errline">Couldn't generate the report — {jobError}</p>
				<button class="linkbtn" onclick={reset}>Try again</button>
			</div>
		{:else}
			<div class="state">
				<form class="customrow" onsubmit={(e) => { e.preventDefault(); pickQuestion(customQuestion); }}>
					<input
						placeholder="Or write your own question…"
						bind:value={customQuestion}
					/>
					<button type="submit" class="iconbtn" title="Generate" disabled={!customQuestion.trim()}>
						<Send size={14} strokeWidth={2} />
					</button>
				</form>

				<p class="section">Suggested for this client</p>
				{#if suggestionsError}
					<p class="dim">Couldn't load suggestions — {suggestionsError}</p>
				{:else if !suggestions}
					<p class="dim skeleton">Thinking of questions worth asking…</p>
				{:else}
					<div class="suggestions">
						{#each suggestions as q}
							<button class="suggestion" onclick={() => pickQuestion(q)}>{q}</button>
						{/each}
					</div>
				{/if}

				{#if pastDebriefs.length}
					<p class="section">Past reports</p>
					<ul class="pastlist">
						{#each pastDebriefs as d}
							<li>
								<button class="pastopen" onclick={() => openReport(d.share_token)}>
									<FileText size={13} strokeWidth={1.9} />
									<span class="pastq">{d.question}</span>
								</button>
								<span class="dim pastdate">{fmtDate(d.created_at)}</span>
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		{/if}
	</div>
</div>

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		background: rgba(20, 14, 6, 0.45);
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 24px;
		z-index: 50;
	}
	.panel {
		width: 100%;
		max-width: 560px;
		max-height: 85vh;
		overflow-y: auto;
		background: var(--paper);
		border: 1.5px solid var(--primary-800);
		box-shadow: var(--shadow-stamp);
		padding: 18px 20px 22px;
	}
	.phead {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-bottom: 14px;
	}
	.picon {
		color: var(--brand);
		display: flex;
	}
	.phead h2 {
		font-family: var(--font-serif);
		font-size: 19px;
		font-weight: 700;
		flex: 1;
	}
	.close {
		background: none;
		border: none;
		cursor: pointer;
		color: var(--mut);
		display: flex;
		padding: 4px;
	}
	.close:hover {
		color: var(--ink);
	}
	.section {
		font-family: var(--font-sans);
		font-size: 11px;
		font-weight: 700;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--mut);
		margin: 16px 0 8px;
	}
	.customrow {
		display: flex;
		gap: 8px;
	}
	.customrow input {
		flex: 1;
		font-family: var(--font-sans);
		font-size: 13.5px;
		padding: 9px 11px;
		border: 1px solid var(--rule);
		background: var(--card);
		color: var(--ink);
	}
	.customrow input:focus {
		outline: none;
		border-color: var(--primary-800);
	}
	.iconbtn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		background: var(--brand);
		color: var(--on-brand);
		border: 1.5px solid var(--primary-900);
		padding: 0 12px;
		cursor: pointer;
	}
	.iconbtn:disabled {
		opacity: 0.5;
		cursor: default;
	}
	.suggestions {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.suggestion {
		text-align: left;
		font-family: var(--font-sans);
		font-size: 13.5px;
		line-height: 1.4;
		padding: 10px 12px;
		border: 1px solid var(--rule);
		background: var(--card);
		cursor: pointer;
		transition:
			border-color 140ms ease,
			background 140ms ease;
	}
	.suggestion:hover {
		border-color: var(--primary-800);
		background: var(--card-2);
	}
	.dim {
		color: var(--mut);
		font-size: 13px;
	}
	.skeleton {
		opacity: 0.7;
		animation: pulse 1.1s ease-in-out infinite;
	}
	@keyframes pulse {
		0%, 100% { opacity: 0.4; }
		50% { opacity: 1; }
	}
	.pastlist {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.pastlist li {
		display: flex;
		align-items: center;
		gap: 8px;
		justify-content: space-between;
	}
	.pastopen {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		background: none;
		border: none;
		cursor: pointer;
		font-family: var(--font-sans);
		font-size: 13px;
		color: var(--ink);
		text-align: left;
		padding: 4px 0;
	}
	.pastopen:hover {
		color: var(--brand-strong);
	}
	.pastq {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		max-width: 340px;
	}
	.pastdate {
		flex: none;
		font-size: 11.5px;
	}
	.qline {
		font-family: var(--font-serif);
		font-style: italic;
		font-size: 16px;
		margin-bottom: 12px;
	}
	.spin {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		font-size: 13.5px;
		color: var(--mut);
	}
	.spin :global(.spinicon) {
		animation: spin 900ms linear infinite;
	}
	@keyframes spin {
		to { transform: rotate(360deg); }
	}
	.progress {
		margin-top: 10px;
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 12.5px;
		color: var(--mut);
	}
	.ready {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		color: var(--inflow);
		font-weight: 700;
		margin-bottom: 10px;
	}
	.linkrow {
		display: flex;
		gap: 6px;
		margin-bottom: 14px;
	}
	.linkrow input {
		flex: 1;
		font-family: var(--font-sans);
		font-size: 12.5px;
		padding: 8px 10px;
		border: 1px solid var(--rule);
		background: var(--card);
		color: var(--ink-2);
	}
	.linkbtn {
		background: none;
		border: none;
		color: var(--brand-strong);
		font-family: var(--font-sans);
		font-size: 13px;
		font-weight: 600;
		cursor: pointer;
		padding: 0;
	}
	.linkbtn:hover {
		text-decoration: underline;
	}
	.errline {
		color: var(--outflow);
		font-size: 13.5px;
		margin-bottom: 12px;
	}
</style>
