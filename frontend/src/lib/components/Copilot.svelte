<script>
	// The Copilot Workspace — chat box → POST /copilot, rendering the narrated answer,
	// the visible tool-call trace (as result cards), and the GPU-vs-CPU timing. Both
	// what-if and stress come back on the same surface: a message in, rich cards back.
	import { tick, onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api, copilotJobEventsUrl } from '$lib/api.js';
	import ToolResult from './ToolResult.svelte';
	import AnswerBody from './AnswerBody.svelte';
	import { Sparkles, Send, MessageSquare, User, Plus, Trash2, History } from '@lucide/svelte';

	// `clientId` scopes "this client" phrasing when embedded on a client page (optional).
	let { clientId = null } = $props();

	const CHIPS = [
		'Who should I call first, and why?',
		'If small-cap fell 20% over 3 months, who couldn’t stomach it?',
		'Give me the brief on Indrani Padmanabhan',
		'For Indrani Padmanabhan, what if she adds ₹8,000/mo to her SIP?'
	];

	let messages = $state([]); // {role, content?, sent?, trace?, elapsed_ms?, backend?, error?}
	let input = $state('');
	let loading = $state(false);
	let toast = $state(null);
	let threadEl;
	let taEl;

	// DB-backed conversation history (the sidebar). currentId is the open chat, or null
	// for an unsaved new chat — set once the first turn is persisted.
	let conversations = $state([]);
	let currentId = $state(null);
	onMount(loadConversations);

	// The open conversation lives in `?c=<id>` so the browser's back/forward buttons
	// step through conversations instead of dumping the user back on the empty state
	// after they navigate away (e.g. to a client page) and return. `syncUrl` is the only
	// writer of `c`; this effect is the only reader, and must react ONLY to the URL
	// changing. `currentId` is read with `untrack` so this effect doesn't also fire when
	// openChat sets currentId (which happens before its goto() resolves) — otherwise the
	// stale `page.url` read during that window disagrees with the new currentId and the
	// effect calls openChat on the *previous* id, which flips currentId back and loops.
	$effect(() => {
		const raw = page.url.searchParams.get('c');
		const cid = raw ? Number(raw) : null;
		if (cid === untrack(() => currentId)) return;
		if (cid) openChat(cid, { push: false });
		else resetChat();
	});

	function syncUrl(id, { push = false } = {}) {
		const params = new URLSearchParams(page.url.searchParams);
		if (id) params.set('c', id);
		else params.delete('c');
		goto(`?${params.toString()}`, { replaceState: !push, keepFocus: true, noScroll: true });
	}

	// A playful "working" status while the (non-streaming) turn runs — cycles phrases so
	// the wait feels like the engine is actually doing something (it is).
	const THINKING = [
		'Reading the book…',
		'Rolling holdings up by category…',
		'Simulating 500,000 market futures…',
		'Reading the tail risk…',
		'Drafting your answer…'
	];
	let thinkingIdx = $state(0);
	$effect(() => {
		if (!loading) {
			thinkingIdx = 0;
			return;
		}
		const id = setInterval(() => (thinkingIdx = (thinkingIdx + 1) % THINKING.length), 1700);
		return () => clearInterval(id);
	});

	function autogrow(el) {
		el.style.height = 'auto';
		el.style.height = Math.min(el.scrollHeight, 160) + 'px';
	}

	// @-mention autocomplete: type "@" to filter clients by name, select to bind name→id.
	// On send, each "@Name" is rewritten inline as "Name (client id N)" so tools get IDs.
	let allClients = $state(null); // lazily fetched [{id, name, risk_profile, portfolio_value}]
	let mentions = $state([]); // resolved {name, id} in the current draft
	let mentionOpen = $state(false);
	let mentionQuery = $state('');
	let mentionStart = $state(-1); // index of the '@' currently being edited
	let activeIdx = $state(0);
	const mentionMatches = $derived(matchClients(mentionQuery, allClients));

	// Prior user/assistant turns, sent back so the loop keeps context. Users turns carry
	// the id-rewritten text (`sent`) so the model sees the same client ids next turn.
	function history() {
		return messages
			.filter((m) => (m.role === 'user' || (m.role === 'assistant' && m.content)) && !m.error)
			.map((m) => ({ role: m.role, content: m.sent ?? m.content ?? '' }));
	}

	async function scrollDown() {
		await tick();
		threadEl?.scrollTo({ top: threadEl.scrollHeight, behavior: 'smooth' });
	}

	// Streams one job's events onto its (already-pushed, reactive) assistant message:
	// tool_call → a pending trace card; tool_result → fills it in; reasoning → a line
	// placed just before the trace card(s) it introduced; final/error → settles the
	// turn. Resolves once the stream ends, however it ends — never rejects, since
	// every path (server error event or a dropped connection) already writes its
	// outcome onto `asst`.
	function streamJob(jobId, asst) {
		return new Promise((resolve) => {
			const es = new EventSource(copilotJobEventsUrl(jobId));
			let settled = false;
			const settle = () => {
				if (settled) return;
				settled = true;
				es.close();
				resolve();
			};
			es.onmessage = async (ev) => {
				let data;
				try {
					data = JSON.parse(ev.data);
				} catch {
					return;
				}
				if (data.type === 'tool_call') {
					asst.trace.push({
						tool_call_id: data.tool_call_id,
						tool: data.tool,
						args: data.args,
						result: null,
						pending: true
					});
				} else if (data.type === 'tool_result') {
					const entry = asst.trace.find((t) => t.tool_call_id === data.tool_call_id);
					if (entry) {
						entry.result = data.result;
						entry.pending = false;
					}
				} else if (data.type === 'reasoning') {
					asst.reasoning.push({ text: data.text, beforeIndex: asst.trace.length });
				} else if (data.type === 'final') {
					asst.content = data.answer;
					asst.elapsed_ms = data.elapsed_ms;
					asst.backend = data.backend;
					asst.streaming = false;
					currentId = data.conversation_id;
					syncUrl(currentId);
					loadConversations(); // refresh titles + ordering
					settle();
				} else if (data.type === 'error') {
					asst.error = true;
					asst.content = data.detail;
					asst.streaming = false;
					settle();
				}
				await scrollDown();
			};
			es.onerror = () => {
				// The connection dropped (not a server-sent "error" event) — the job may
				// still be running server-side, but the live view can't continue.
				if (asst.streaming) {
					asst.streaming = false;
					asst.error = true;
					asst.content = 'Lost connection to the Copilot job.';
				}
				settle();
			};
		});
	}

	async function send(text) {
		const raw = (text ?? input).trim();
		if (!raw || loading) return;
		closeMentions();
		const { msg, ids } = rewriteMentions(raw);
		input = '';
		mentions = [];
		if (taEl) taEl.style.height = 'auto'; // reset the auto-grown composer
		messages.push({ role: 'user', content: raw, sent: msg });
		const priorHistory = history();
		messages.push({ role: 'assistant', trace: [], reasoning: [], streaming: true });
		const asst = messages[messages.length - 1]; // the reactive item, not the plain literal above
		loading = true;
		await scrollDown();
		// A single mentioned client also scopes "this client" phrasing; else keep the prop.
		const client_id = ids.length === 1 ? ids[0] : clientId;
		try {
			const { job_id } = await api.startCopilotJob({
				message: msg,
				display_message: raw,
				history: priorHistory,
				client_id,
				conversation_id: currentId
			});
			await streamJob(job_id, asst);
		} catch (e) {
			asst.error = true;
			asst.content = e.message;
			asst.streaming = false;
		} finally {
			loading = false;
			await scrollDown();
		}
	}

	// ── Conversation history ────────────────────────────────────────────────────
	async function loadConversations() {
		try {
			conversations = await api.listConversations();
		} catch {
			/* history is best-effort; a fresh/offline DB just shows an empty list */
		}
	}

	function resetChat() {
		messages = [];
		mentions = [];
		input = '';
		currentId = null;
		closeMentions();
	}

	function newChat() {
		resetChat();
		syncUrl(null, { push: true });
	}

	async function openChat(id, { push = true } = {}) {
		if (id === currentId) return;
		try {
			const conv = await api.getConversation(id);
			messages = conv.messages.map((m) => ({ ...m }));
			currentId = id;
			mentions = [];
			input = '';
			closeMentions();
			syncUrl(id, { push });
			await scrollDown();
		} catch (e) {
			toast = `Couldn't open conversation: ${e.message}`;
			setTimeout(() => (toast = null), 4000);
		}
	}

	async function removeChat(id, e) {
		e.stopPropagation();
		try {
			await api.deleteConversation(id);
			if (id === currentId) newChat();
			await loadConversations();
		} catch (err) {
			toast = `Delete failed: ${err.message}`;
			setTimeout(() => (toast = null), 4000);
		}
	}

	// Compact relative time for the sidebar (e.g. "just now", "4m", "3h", "2d", or a date).
	function relTime(iso) {
		const then = new Date(iso).getTime();
		const secs = Math.max(0, (Date.now() - then) / 1000);
		if (secs < 60) return 'just now';
		if (secs < 3600) return `${Math.floor(secs / 60)}m`;
		if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
		if (secs < 604800) return `${Math.floor(secs / 86400)}d`;
		return new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
	}

	function onKey(e) {
		if (mentionOpen && mentionMatches.length) {
			if (e.key === 'ArrowDown') {
				e.preventDefault();
				activeIdx = (activeIdx + 1) % mentionMatches.length;
				return;
			}
			if (e.key === 'ArrowUp') {
				e.preventDefault();
				activeIdx = (activeIdx - 1 + mentionMatches.length) % mentionMatches.length;
				return;
			}
			if (e.key === 'Enter' || e.key === 'Tab') {
				e.preventDefault();
				selectMention(mentionMatches[activeIdx]);
				return;
			}
			if (e.key === 'Escape') {
				e.preventDefault();
				closeMentions();
				return;
			}
		}
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			send();
		}
	}

	// ── @-mention machinery ────────────────────────────────────────────────────
	async function ensureClients() {
		if (allClients) return;
		try {
			const rows = await api.listClients({ limit: 500 });
			allClients = rows.map((r) => ({
				id: r.id,
				name: r.name,
				risk_profile: r.risk_profile,
				portfolio_value: r.portfolio_value
			}));
		} catch {
			allClients = [];
		}
	}

	// Every whitespace token of the query must be a substring of the name; startsWith wins.
	function matchClients(q, list) {
		if (!list) return [];
		const tokens = q.toLowerCase().split(/\s+/).filter(Boolean);
		const scored = [];
		for (const c of list) {
			const name = c.name.toLowerCase();
			if (tokens.every((t) => name.includes(t))) {
				scored.push({ c, starts: name.startsWith(tokens[0] ?? '') ? 1 : 0 });
			}
		}
		scored.sort((a, b) => b.starts - a.starts || a.c.name.length - b.c.name.length);
		return scored.slice(0, 8).map((s) => s.c);
	}

	function closeMentions() {
		mentionOpen = false;
		mentionQuery = '';
		mentionStart = -1;
	}

	function handleInput(e) {
		const el = e.currentTarget;
		autogrow(el);
		const caret = el.selectionStart ?? 0;
		const before = el.value.slice(0, caret);
		const m = before.match(/(^|\s)@([^\n@]*)$/); // '@' at start/after space, up to caret
		if (!m) return closeMentions();
		const query = m[2];
		// Suppress once a mention is resolved and the caret has moved past it.
		if (mentions.some((mn) => query === mn.name || query.startsWith(mn.name + ' '))) {
			return closeMentions();
		}
		ensureClients();
		mentionQuery = query;
		mentionStart = caret - query.length - 1;
		mentionOpen = true;
		activeIdx = 0;
	}

	async function selectMention(c) {
		if (mentionStart < 0) return;
		const caret = taEl.selectionStart ?? input.length;
		const head = input.slice(0, mentionStart);
		const tail = input.slice(caret);
		const token = `@${c.name} `;
		input = head + token + tail;
		if (!mentions.some((mn) => mn.id === c.id)) mentions = [...mentions, { name: c.name, id: c.id }];
		closeMentions();
		await tick();
		const pos = (head + token).length;
		taEl.focus();
		taEl.setSelectionRange(pos, pos);
	}

	// Rewrite "@Name" → "Name (client id N)" for each resolved mention present in the text.
	function rewriteMentions(text) {
		let msg = text;
		const ids = [];
		for (const mn of mentions) {
			const tag = '@' + mn.name;
			if (msg.includes(tag)) {
				msg = msg.split(tag).join(`${mn.name} (client id ${mn.id})`);
				if (!ids.includes(mn.id)) ids.push(mn.id);
			}
		}
		return { msg, ids };
	}

	async function handleCommit(cid, rows) {
		try {
			const res = await api.commitTransactions(cid, rows);
			toast = `Committed ${res.inserted} transaction${res.inserted === 1 ? '' : 's'} to the ledger.`;
		} catch (e) {
			toast = `Commit failed: ${e.message}`;
		}
		setTimeout(() => (toast = null), 4000);
	}
</script>

<div class="workspace">
	<aside class="sidebar">
		<div class="convhead"><History size={12} strokeWidth={2} /> History</div>
		<div class="convlist">
			{#if conversations.length === 0}
				<p class="convempty">No conversations yet. Ask something to start one.</p>
			{/if}
			{#each conversations as conv (conv.id)}
				<div class="convrow" class:on={conv.id === currentId}>
					<button class="convmain" onclick={() => openChat(conv.id)}>
						<span class="convtitle">{conv.title}</span>
						<span class="convtime">{relTime(conv.updated_at)} · {conv.message_count} msg</span>
					</button>
					<button
						class="convdel"
						aria-label="Delete conversation"
						onclick={(e) => removeChat(conv.id, e)}
					>
						<Trash2 size={13} strokeWidth={2} />
					</button>
				</div>
			{/each}
		</div>
	</aside>

	<div class="copilot">
		<div class="cophead">
			<div class="titlewrap">
				<span class="titleicon"><Sparkles size={36} strokeWidth={1.5} /></span>
				<h1 class="title">AI Advisor</h1>
			</div>
			<button class="newchat" onclick={newChat}>
				<Plus size={15} strokeWidth={2} /> New chat
			</button>
		</div>
		<div class="titlerule"></div>

	<div class="thread" bind:this={threadEl}>
		{#if messages.length === 0}
			<div class="empty">
				<h2>Ask the book anything.</h2>
				<p class="h2sub">
					The Copilot reasons over your whole book and each client, calling five tools —
					<b>query_book</b>, <b>get_client_brief</b>, <b>run_whatif</b>, <b>stress_book</b>,
					<b>rank_book</b> — and narrates what it finds. Every tool call and its result show
					up right here.
				</p>
				<div class="chips">
					{#each CHIPS as c}
						<button class="chip" onclick={() => send(c)}>
							<MessageSquare size={14} strokeWidth={1.8} />{c}
						</button>
					{/each}
				</div>
			</div>
		{/if}

		{#each messages as m}
			{#if m.role === 'user'}
				<div class="turn user"><div class="bubble">{m.content}</div></div>
			{:else}
				<div class="turn asst">
					<div class="avatar" class:thinkingav={m.streaming && !m.trace?.length}>
						<Sparkles size={15} strokeWidth={2} />
					</div>
					<div class="body">
						{#if m.trace?.length}
							<div class="trace">
								{#each m.trace as entry, i}
									{#each (m.reasoning ?? []).filter((r) => r.beforeIndex === i) as r}
										<p class="reasoning">{r.text}</p>
									{/each}
									<ToolResult {entry} index={i} pending={entry.pending} oncommit={handleCommit} />
								{/each}
							</div>
						{/if}
						{#if m.error}
							<div class="answer err">{m.content}</div>
						{:else if m.content}
							<div class="answer"><AnswerBody text={m.content} /></div>
						{:else if m.streaming}
							<div class="thinking">
								{#if m.trace?.length}
									<span class="tphrase">Reading the tool results…</span>
								{:else}
									{#key thinkingIdx}<span class="tphrase">{THINKING[thinkingIdx]}</span>{/key}
								{/if}
								<span class="tdots"><i></i><i></i><i></i></span>
							</div>
						{/if}
						{#if m.elapsed_ms != null}
							<div class="meta">{m.backend} · {m.elapsed_ms} ms</div>
						{/if}
					</div>
				</div>
			{/if}
		{/each}
	</div>

	{#if toast}<div class="toast">{toast}</div>{/if}

	<div class="composer">
		{#if mentionOpen && mentionMatches.length}
			<div class="mentionpop">
				<div class="mhint">Mention a client</div>
				{#each mentionMatches as c, i (c.id)}
					<button
						class="mrow"
						class:on={i === activeIdx}
						onmousedown={(e) => {
							e.preventDefault();
							selectMention(c);
						}}
						onmouseenter={() => (activeIdx = i)}
					>
						<span class="mavatar"><User size={13} strokeWidth={2} /></span>
						<span class="mname">{c.name}</span>
						<span class="mmeta">#{c.id} · {c.risk_profile}</span>
					</button>
				{/each}
			</div>
		{/if}
		<textarea
			bind:this={taEl}
			bind:value={input}
			oninput={handleInput}
			onkeydown={onKey}
			placeholder="Ask about the book, a what-if, a market shock… type @ to mention a client"
			rows="1"
		></textarea>
		<button class="sendbtn" onclick={() => send()} disabled={loading || !input.trim()}>
			<Send size={16} strokeWidth={2} />
		</button>
	</div>
	</div>
</div>

<style>
	.workspace {
		display: flex;
		gap: 0;
		height: calc(100vh - 70px);
		max-width: 1480px;
		margin: 0 auto;
	}
	/* ── History sidebar ── */
	.sidebar {
		flex: none;
		width: 258px;
		display: flex;
		flex-direction: column;
		border-right: 1.5px solid var(--primary-300);
		padding: 16px 12px 16px 16px;
		min-height: 0;
	}
	.convhead {
		display: flex;
		align-items: center;
		gap: 6px;
		font-family: var(--font-sans);
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.16em;
		text-transform: uppercase;
		color: var(--mut);
		margin: 4px 4px 8px;
	}
	.convlist {
		flex: 1;
		overflow-y: auto;
		min-height: 0;
		display: flex;
		flex-direction: column;
		gap: 3px;
	}
	/* Ledger-style scrollbar: square, ink-on-cream, matching the stamp aesthetic. */
	.convlist,
	.thread {
		scrollbar-width: thin;
		scrollbar-color: var(--primary-600) transparent;
	}
	.convlist::-webkit-scrollbar,
	.thread::-webkit-scrollbar {
		width: 9px;
	}
	.convlist::-webkit-scrollbar-track,
	.thread::-webkit-scrollbar-track {
		background: transparent;
	}
	.convlist::-webkit-scrollbar-thumb,
	.thread::-webkit-scrollbar-thumb {
		background-color: var(--primary-600);
		border: 2px solid var(--primary-100);
		background-clip: padding-box;
	}
	.convlist::-webkit-scrollbar-thumb:hover,
	.thread::-webkit-scrollbar-thumb:hover {
		background-color: var(--primary-800);
	}
	.convempty {
		font-size: 12px;
		color: var(--faint);
		padding: 8px 4px;
		line-height: 1.5;
	}
	.convrow {
		display: flex;
		align-items: stretch;
		border: 1.5px solid transparent;
	}
	.convrow.on {
		border-color: var(--primary-800);
		background: var(--card-2);
		box-shadow: var(--shadow-stamp-sm);
	}
	.convrow:hover:not(.on) {
		background: var(--primary-100);
	}
	.convmain {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
		background: transparent;
		border: none;
		cursor: pointer;
		text-align: left;
		padding: 8px 6px 8px 9px;
		font-family: var(--font-sans);
	}
	.convtitle {
		font-size: 13px;
		font-weight: 600;
		color: var(--ink);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.convtime {
		font-size: 10.5px;
		color: var(--mut);
		font-variant-numeric: tabular-nums;
	}
	.convdel {
		flex: none;
		display: grid;
		place-items: center;
		width: 30px;
		background: transparent;
		border: none;
		color: var(--faint);
		cursor: pointer;
		opacity: 0;
		transition: opacity 120ms ease;
	}
	.convrow:hover .convdel,
	.convrow.on .convdel {
		opacity: 1;
	}
	.convdel:hover {
		color: var(--outflow);
	}
	.copilot {
		flex: 1;
		min-width: 0;
		max-width: 1100px;
		margin: 0 auto;
		padding: 12px 40px 24px;
		display: flex;
		flex-direction: column;
		height: 100%;
	}
	.cophead {
		display: flex;
		align-items: flex-end;
		justify-content: space-between;
		gap: 16px;
		flex-wrap: wrap;
	}
	.newchat {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		font-family: var(--font-sans);
		font-size: 11px;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		white-space: nowrap;
		background: var(--brand);
		color: var(--on-brand);
		border: 1.5px solid var(--primary-900);
		box-shadow: var(--shadow-stamp-sm);
		padding: 9px 14px;
		cursor: pointer;
		margin-bottom: 4px;
	}
	.newchat:hover {
		transform: translate(-1px, -1px);
		box-shadow: var(--shadow-stamp);
	}
	.titlerule {
		margin: 14px 0 12px;
	}
	.thread {
		flex: 1;
		overflow-y: auto;
		padding: 4px 4px 10px;
	}
	.empty {
		padding: 30px 0 10px;
	}
	.empty h2 {
		font-family: var(--font-serif);
		font-style: italic;
		font-weight: 500;
		font-size: 26px;
		color: var(--ink);
		margin: 0 0 6px;
	}
	.empty .h2sub {
		color: var(--mut);
		font-size: 14px;
		max-width: 720px;
		margin: 0 0 20px;
	}
	.empty .h2sub b {
		color: var(--brand-strong);
		font-weight: 600;
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 10px;
	}
	.empty h2 {
		animation: fadeUp 0.4s var(--ease-out) both;
	}
	.empty .h2sub {
		animation: fadeUp 0.4s var(--ease-out) 0.05s both;
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
		cursor: pointer;
		text-align: left;
		transition:
			transform 0.12s var(--ease-out),
			box-shadow 0.12s var(--ease-out);
		animation: fadeUp 0.4s var(--ease-out) both;
	}
	.chip:nth-child(1) {
		animation-delay: 0.12s;
	}
	.chip:nth-child(2) {
		animation-delay: 0.19s;
	}
	.chip:nth-child(3) {
		animation-delay: 0.26s;
	}
	.chip:nth-child(4) {
		animation-delay: 0.33s;
	}
	.chip:hover {
		transform: translate(-1px, -1px);
		box-shadow: var(--shadow-stamp);
	}
	.chip :global(svg) {
		color: var(--brand);
		flex: none;
	}
	.turn {
		margin: 16px 0;
		display: flex;
		animation: fadeUp 0.36s var(--ease-out) both;
	}
	@keyframes fadeUp {
		from {
			opacity: 0;
			transform: translateY(10px);
		}
		to {
			opacity: 1;
			transform: none;
		}
	}
	.turn.user {
		justify-content: flex-end;
	}
	.turn.user .bubble {
		background: var(--brand);
		color: var(--on-brand);
		padding: 10px 15px;
		max-width: 75%;
		border: 1.5px solid var(--primary-900);
		box-shadow: var(--shadow-stamp-sm);
		font-size: 14.5px;
		white-space: pre-wrap;
	}
	.turn.asst {
		gap: 12px;
		align-items: flex-start;
	}
	.avatar {
		flex: none;
		width: 30px;
		height: 30px;
		display: grid;
		place-items: center;
		background: var(--paper);
		color: var(--brand);
		border: 1.5px solid var(--primary-800);
		box-shadow: var(--shadow-stamp-sm);
	}
	.body {
		flex: 1;
		min-width: 0;
	}
	.trace {
		margin-bottom: 6px;
	}
	.reasoning {
		font-family: var(--font-serif);
		font-style: italic;
		font-size: 13.5px;
		color: var(--mut);
		margin: 8px 2px 6px;
		animation: fadeUp 0.3s var(--ease-out) both;
	}
	.answer {
		font-size: 14.5px;
		color: var(--ink);
		line-height: 1.65;
		background: var(--paper);
		border: 1.5px solid var(--primary-800);
		box-shadow: var(--shadow-stamp);
		padding: 14px 18px;
	}
	.answer.err {
		color: var(--secondary-700);
		font-size: 13.5px;
		background: var(--outflow-soft);
		border-color: var(--outflow);
	}
	.meta {
		font-size: 10.5px;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--inflow);
		margin-top: 8px;
	}
	.thinking {
		display: inline-flex;
		align-items: center;
		gap: 11px;
		font-size: 13.5px;
		color: var(--mut);
		font-style: italic;
		padding-top: 5px;
	}
	.tphrase {
		display: inline-block;
		animation: fadeUp 0.35s var(--ease-out) both;
	}
	.tdots {
		display: inline-flex;
		gap: 4px;
	}
	.tdots i {
		width: 5px;
		height: 5px;
		border-radius: 50%;
		background: var(--brand);
		display: inline-block;
		animation: bounce 1s ease-in-out infinite;
	}
	.tdots i:nth-child(2) {
		animation-delay: 0.15s;
	}
	.tdots i:nth-child(3) {
		animation-delay: 0.3s;
	}
	.thinkingav :global(svg) {
		animation: breathe 1.7s ease-in-out infinite;
	}
	@keyframes bounce {
		0%,
		80%,
		100% {
			transform: translateY(0);
			opacity: 0.35;
		}
		40% {
			transform: translateY(-5px);
			opacity: 1;
		}
	}
	@keyframes breathe {
		0%,
		100% {
			opacity: 0.55;
			transform: scale(0.9);
		}
		50% {
			opacity: 1;
			transform: scale(1.08);
		}
	}
	.toast {
		position: sticky;
		bottom: 74px;
		align-self: center;
		background: var(--inflow);
		color: #fff;
		padding: 9px 16px;
		font-size: 13px;
		font-weight: 600;
		border: 1.5px solid var(--primary-900);
		box-shadow: var(--shadow-stamp);
		margin-bottom: 8px;
		animation: fadeUp 0.3s var(--ease-out) both;
	}
	/* Conversation rows: gentle tint transition on hover/active. */
	.convrow {
		transition:
			background 0.12s ease,
			border-color 0.12s ease;
	}
	.composer {
		position: relative;
		display: flex;
		gap: 10px;
		align-items: flex-end;
		border: 1.5px solid var(--primary-800);
		background: var(--paper);
		box-shadow: var(--shadow-stamp);
		padding: 8px 8px 8px 14px;
		transition:
			box-shadow 0.18s var(--ease-out),
			border-color 0.18s var(--ease-out);
	}
	.composer:focus-within {
		border-color: var(--primary-900);
		box-shadow: var(--shadow-stamp-lg);
	}
	.mentionpop {
		position: absolute;
		bottom: calc(100% + 8px);
		left: 0;
		width: min(380px, 90%);
		max-height: 280px;
		overflow-y: auto;
		background: var(--paper);
		border: 1.5px solid var(--primary-800);
		box-shadow: var(--shadow-stamp);
		z-index: 30;
		padding: 5px;
		transform-origin: bottom left;
		animation: popIn 0.16s var(--ease-out) both;
	}
	.mrow {
		transition: background 0.1s ease;
	}
	@keyframes popIn {
		from {
			opacity: 0;
			transform: translateY(6px) scale(0.98);
		}
		to {
			opacity: 1;
			transform: none;
		}
	}
	.mhint {
		font-family: var(--font-sans);
		font-size: 10px;
		font-weight: 600;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--mut);
		padding: 6px 8px 8px;
	}
	.mrow {
		width: 100%;
		display: flex;
		align-items: center;
		gap: 9px;
		padding: 8px 9px;
		background: transparent;
		border: none;
		cursor: pointer;
		text-align: left;
		font-family: var(--font-sans);
	}
	.mrow.on {
		background: var(--card-2);
	}
	.mavatar {
		flex: none;
		width: 24px;
		height: 24px;
		display: grid;
		place-items: center;
		background: var(--paper);
		color: var(--brand);
		border: 1.5px solid var(--primary-800);
	}
	.mrow.on .mavatar {
		background: var(--brand);
		color: var(--on-brand);
	}
	.mname {
		flex: 1;
		font-size: 14px;
		font-weight: 600;
		color: var(--ink);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.mmeta {
		flex: none;
		font-size: 11px;
		color: var(--mut);
		font-variant-numeric: tabular-nums;
	}
	.composer textarea {
		flex: 1;
		border: none;
		background: transparent;
		resize: none;
		font-family: var(--font-sans);
		font-size: 15px;
		color: var(--ink);
		line-height: 1.5;
		max-height: 140px;
		padding: 6px 0;
		outline: none;
	}
	.sendbtn {
		flex: none;
		width: 42px;
		height: 42px;
		display: grid;
		place-items: center;
		background: var(--brand);
		color: var(--on-brand);
		border: 1.5px solid var(--primary-900);
		box-shadow: var(--shadow-stamp-sm);
		cursor: pointer;
	}
	.sendbtn:disabled {
		opacity: 0.45;
		cursor: default;
	}
	.sendbtn {
		transition:
			transform 0.12s var(--ease-out),
			box-shadow 0.12s var(--ease-out);
	}
	.sendbtn:not(:disabled):hover {
		transform: translate(-1px, -1px);
		box-shadow: var(--shadow-stamp);
	}
	.sendbtn:not(:disabled):active {
		transform: translate(1px, 1px);
		box-shadow: var(--shadow-stamp-sm);
	}
	@media (max-width: 860px) {
		.sidebar {
			display: none;
		}
	}
	@media (max-width: 700px) {
		.copilot {
			padding: 12px 16px 20px;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.turn,
		.toast,
		.mentionpop,
		.empty h2,
		.empty .h2sub,
		.chip,
		.tphrase,
		.thinkingav :global(svg),
		.tdots i,
		.reasoning {
			animation: none !important;
		}
	}
</style>
