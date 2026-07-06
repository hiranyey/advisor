<script>
	// Advisor Dashboard (Screen A landing page) — "what do I need to do today, and why,"
	// leading with an LLM-narrated briefing + insight cards, then an urgency-grouped
	// action feed and two at-a-glance visualizations. The old heatmap/histogram (still
	// genuinely useful) move to a collapsible "detailed analytics" section below the fold.
	import { goto } from '$app/navigation';
	import { fly } from 'svelte/transition';
	import { api, inr, pct, catLabel, catColor, fmtDate } from '$lib/api.js';
	import AllocationBreakdown from '$lib/components/AllocationBreakdown.svelte';
	import RiskPill from '$lib/components/RiskPill.svelte';
	import Briefing from '$lib/components/Briefing.svelte';
	import InsightCard from '$lib/components/InsightCard.svelte';
	import BookTrendChart from '$lib/components/BookTrendChart.svelte';
	import AllocationTrendChart from '$lib/components/AllocationTrendChart.svelte';
	import RiskQuadrant from '$lib/components/RiskQuadrant.svelte';
	import { LayoutDashboard, Layers, TriangleAlert, PhoneCall, Eye, ShieldCheck } from '@lucide/svelte';

	let radar = $state(null);
	let summary = $state(null);
	let loading = $state(true);
	let error = $state(null);

	// Time-of-day heading — a small "someone's actually here" touch on an otherwise
	// static dashboard title. Computed once on mount, not reactively per-second.
	const TITLES = [
		{ maxHour: 12, text: 'Your book, this morning' },
		{ maxHour: 17, text: 'Your book, this afternoon' },
		{ maxHour: 21, text: 'Your book, this evening' },
		{ maxHour: 24, text: 'The nightly close' }
	];
	const pageTitle = TITLES.find((t) => new Date().getHours() < t.maxHour)?.text ?? 'Your book, today';

	// Secondary, non-blocking loads — the core numbers render even if these are slow.
	let insights = $state(null);
	let trend = $state(null);
	let allocTrend = $state(null);

	// Per-category share change since the first scored run — powers the chips in the
	// allocation list.
	const allocDeltas = $derived(
		Object.fromEntries((allocTrend?.deltas ?? []).map((d) => [d.category, d.weight_change]))
	);

	$effect(() => {
		load();
	});

	async function load() {
		loading = true;
		error = null;
		try {
			[radar, summary] = await Promise.all([api.bookRadar(), api.bookSummary()]);
		} catch (e) {
			error = e.message;
		} finally {
			loading = false;
		}
		loadInsights();
		api.bookTrend().then((r) => (trend = r)).catch(() => {});
		api.bookAllocationTrend().then((r) => (allocTrend = r)).catch(() => {});
	}

	function loadInsights() {
		api.bookInsights().then((r) => (insights = r)).catch(() => {});
	}

	async function refreshInsights() {
		insights = await api.refreshBookInsights();
	}

	const rise = (i) => ({ y: 10, duration: 240, delay: i * 55 });

	// Heatmap cell state → design-system severity class + plain-language label.
	const CELL = {
		ok: { cls: 'ok', label: 'Fine' },
		tight: { cls: 'low', label: 'At limit' },
		breach: { cls: 'bad', label: 'Too risky' }
	};
	const flagLabel = {
		concentrated_fund: 'Too much in one fund',
		concentrated_category: 'Too much in one type',
		off_track: 'Behind on a goal'
	};
	const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : '—');

	// Goal-success histogram: rose (unlikely) → gold → green (on-track). Semantic, not decorative.
	const HIST_COLORS = ['#a8224a', '#c2542a', '#d4a017', '#8a9b3a', '#5f8a3f', '#4a7c3a'];
	const histMax = $derived(
		radar ? Math.max(1, ...radar.goal_success_hist.map((b) => b.count)) : 1
	);

	// worst-case downside displayed as a signed percentage
	const neg = (x) => (x == null ? '—' : `−${(x * 100).toFixed(1)}%`);

	// ── Action feed: the call list regrouped by urgency instead of one flat table ──
	const grouped = $derived.by(() => {
		if (!radar) return { breach: [], watch: [] };
		return {
			breach: radar.call_list.filter((c) => c.status === 'breach'),
			watch: radar.call_list.filter((c) => c.status === 'watch')
		};
	});
	// "Healthy" is a book-wide count (kpis), not just whatever's left in the top-25 slice.
	const healthyCount = $derived(
		radar ? radar.clients_scored - radar.kpis.mismatches - radar.kpis.watch : 0
	);

	// client_id → name, for the insight cards' client chips (covers the whole book, not
	// just the top-25 call list).
	const clientNames = $derived(
		radar ? Object.fromEntries(radar.scatter.map((p) => [p.client_id, p.name])) : {}
	);
</script>

{#snippet actionCols()}
	<colgroup>
		<col style="width:18%" />
		<col style="width:9%" />
		<col style="width:15%" />
		<col style="width:42%" />
		<col style="width:16%" />
	</colgroup>
{/snippet}

{#snippet actionHead()}
	<tr>
		<th>Client</th>
		<th>Risk they want</th>
		<th class="r">Could lose</th>
		<th>Why call them</th>
		<th>Goal at risk</th>
	</tr>
{/snippet}

{#snippet actionRow(c)}
	<tr class="rowlink" onclick={() => goto(`/clients/${c.client_id}`)}>
		<td class="name">{c.name}</td>
		<td><RiskPill profile={c.risk_profile} /></td>
		<td class="r">
			{#if c.mismatch > 0}
				<span class="gap pos"
					>−{inr(c.simulated_dd * c.portfolio_value)}<small
						>{inr(Math.abs(c.mismatch) * c.portfolio_value)} over limit</small
					></span
				>
			{:else}
				<span class="gap left">−{inr(c.simulated_dd * c.portfolio_value)}<small>in a bad year</small></span>
			{/if}
		</td>
		<td class="reasoncell">
			{#if c.reason}
				<span class="reasontext">{c.reason}</span>
			{:else}
				<div class="driver">
					<span class="drv-exp" style="color:{catColor(c.top_category)}">
						<Layers size={13} strokeWidth={2} />
						{pct(c.top_weight)} {catLabel(c.top_category ?? 'other')}
					</span>
					<span class="drv-flags">
						{#each c.flags.filter((f) => f !== 'off_track') as f}
							<span class="miniflag">{flagLabel[f] ?? f}</span>
						{/each}
					</span>
				</div>
			{/if}
		</td>
		<td>
			{#if c.worst_goal_name}
				<span class="wg">{c.worst_goal_name}</span>
				<span class="dim num"> {pct(c.worst_goal_prob)}</span>
			{:else}
				<span class="dim">—</span>
			{/if}
		</td>
	</tr>
{/snippet}

<div class="page">
	<p class="eyebrow">Advisor Dashboard</p>
	<div class="titlewrap">
		<span class="titleicon"><LayoutDashboard size={44} strokeWidth={1.5} /></span>
		<h1 class="title">{pageTitle}</h1>
	</div>
	<div class="titlerule"></div>

	{#if loading}
		<div class="loading">Running the book through the engine…</div>
	{:else if error}
		<div class="errbox">Couldn't reach the API — {error}</div>
	{:else if radar && radar.clients_scored > 0}
		<p class="runline">
			<span class="dot"></span>
			<span>Analysis up to date</span> ·
			<b>{radar.clients_scored} clients</b> · every portfolio tested against
			{radar.n_paths?.toLocaleString('en-IN')} market scenarios · updated {fmtDate(radar.as_of_date)}
			<span class="slow">(projections, not guarantees)</span>
		</p>

		<!-- AI morning briefing -->
		<div class="section" in:fly={rise(0)}>
			<Briefing {insights} onRefresh={refreshInsights} />
		</div>

		<!-- AI insight cards — top 3, evenly spaced -->
		{#if insights?.insights?.length}
			<div class="insightgrid" in:fly={rise(1)}>
				{#each insights.insights.slice(0, 3) as ins}
					<InsightCard insight={ins} {clientNames} />
				{/each}
			</div>
		{/if}

		<div class="grid">
			<!-- Action feed — grouped by urgency instead of one flat table -->
			<div class="card full" in:fly={rise(3)}>
				<h2>Who to call first</h2>
				<p class="h2sub">Grouped by urgency, most urgent at the top. Click a row to open the client.</p>
				<div class="h2rule"></div>

				{#if grouped.breach.length}
					<div class="actiongroup">
						<p class="grouphead crit">
							<PhoneCall size={14} strokeWidth={2.2} /> Call today
							<span class="groupcount">{grouped.breach.length}</span>
						</p>
						<div class="tablescroll">
							<table class="actiontable">
								{@render actionCols()}
								<thead>
									{@render actionHead()}
								</thead>
								<tbody>
									{#each grouped.breach as c}{@render actionRow(c)}{/each}
								</tbody>
							</table>
						</div>
					</div>
				{/if}

				{#if grouped.watch.length}
					<div class="actiongroup">
						<p class="grouphead warn">
							<Eye size={14} strokeWidth={2.2} /> Review this week
							<span class="groupcount">{grouped.watch.length}</span>
						</p>
						<div class="tablescroll">
							<table class="actiontable">
								{@render actionCols()}
								<thead>
									{@render actionHead()}
								</thead>
								<tbody>
									{#each grouped.watch as c}{@render actionRow(c)}{/each}
								</tbody>
							</table>
						</div>
					</div>
				{/if}

				<p class="grouphead good">
					<ShieldCheck size={14} strokeWidth={2.2} /> Healthy — no action needed
					<span class="groupcount">{healthyCount}</span>
				</p>
			</div>

			<!-- Risk vs goal-success quadrant -->
			<div class="card full" in:fly={rise(5)}>
				<h2>Risk vs. goals, whole book</h2>
				<p class="h2sub">
					Every scored client — right of center means riskier than they're comfortable with; below
					the gold line means their toughest goal is unlikely on the current plan. Click a dot to
					open that client.
				</p>
				<div class="h2rule"></div>
				<RiskQuadrant points={radar.scatter} />
			</div>

			<!-- Book-wide allocation (context) -->
			{#if summary}
				<div class="card full" in:fly={rise(6)}>
					<h2>Book-wide allocation</h2>
					<p class="h2sub">
						Every client's holdings rolled up by category — the 14 "asset classes" the engine
						simulates. Book AUM {inr(summary.total_aum)} across {summary.total_clients} clients.
					</p>
					<div class="h2rule"></div>

					{#if allocTrend?.points?.length}
						<div class="alloc-over-time">
							<p class="minihead">How the mix moved over time</p>
							<!-- <AllocationTrendChart points={allocTrend.points} /> -->
							{#if allocTrend.insights?.length}
								<ul class="alloc-insights">
									{#each allocTrend.insights as line}
										<li>{line}</li>
									{/each}
								</ul>
							{/if}
						</div>
						<p class="minihead">Where it stands today</p>
					{/if}

					<AllocationBreakdown allocation={summary.allocation} deltas={allocDeltas} />
				</div>
			{/if}
		</div>
	{:else}
		<div class="errbox">
			No analysis yet. Run <code>uv run python -m app.tasks.baseline</code> (or re-seed) to score the
			book.
		</div>
	{/if}
</div>

<style>
	.section {
		margin-bottom: 18px;
	}
	.alloc-over-time {
		margin-bottom: 18px;
	}
	.minihead {
		font-family: var(--font-sans);
		font-size: 10.5px;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--faint);
		margin: 0 0 8px;
	}
	.alloc-over-time + .minihead {
		margin-top: 4px;
		padding-top: 14px;
		border-top: 1px dashed var(--rule);
	}
	.alloc-insights {
		list-style: none;
		margin: 12px 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 5px;
	}
	.alloc-insights li {
		position: relative;
		padding-left: 15px;
		font-size: 13px;
		color: var(--ink-2);
		line-height: 1.45;
	}
	.alloc-insights li::before {
		content: '';
		position: absolute;
		left: 2px;
		top: 8px;
		width: 5px;
		height: 5px;
		background: var(--primary-500, var(--primary-800));
		border-radius: 50%;
	}
	.insightgrid {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 14px;
		margin-bottom: 22px;
	}
	@media (max-width: 900px) {
		.insightgrid {
			grid-template-columns: 1fr;
		}
	}

	/* Action feed groups */
	.actiongroup {
		margin-bottom: 22px;
	}
	.grouphead {
		display: flex;
		align-items: center;
		gap: 7px;
		font-family: var(--font-sans);
		font-size: 11.5px;
		font-weight: 700;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		margin-bottom: 10px;
	}
	.grouphead.crit {
		color: var(--outflow);
	}
	.grouphead.warn {
		color: var(--primary-800);
	}
	.grouphead.good {
		color: var(--inflow);
	}
	.groupcount {
		font-family: var(--font-serif);
		font-weight: 800;
		font-size: 14px;
		background: var(--card-2);
		border: 1px solid var(--primary-300);
		padding: 1px 8px;
		letter-spacing: 0;
		text-transform: none;
	}
	@keyframes growbar {
		from {
			transform: scaleX(0);
		}
		to {
			transform: scaleX(1);
		}
	}

	/* Action feed table */
	.tablescroll {
		overflow-x: auto;
	}
	/* Fixed column widths (see actionCols snippet) so a short reason and a long one hold
	   the same row shape — the text wraps within its column instead of the column
	   stretching to fit, which is what made row widths jump around before. */
	.actiontable {
		table-layout: fixed;
		min-width: 760px;
	}
	.reasoncell {
		white-space: normal;
		line-height: 1.4;
	}
	.reasontext {
		font-size: 13.5px;
		color: var(--ink-2);
	}
	.gap {
		display: inline-flex;
		flex-direction: column;
		align-items: flex-end;
		line-height: 1.15;
		font-family: var(--font-serif);
		font-weight: 800;
		font-variant-numeric: tabular-nums;
		font-size: 16px;
	}
	.gap.pos {
		color: var(--outflow);
	}
	.gap.left {
		color: var(--primary-800);
	}
	.gap small {
		font-family: var(--font-sans);
		font-weight: 600;
		font-size: 8.5px;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--faint);
	}
	.driver {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.drv-exp {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		font-size: 13px;
		font-weight: 600;
		white-space: nowrap;
	}
	.drv-flags {
		display: flex;
		gap: 5px;
		flex-wrap: wrap;
	}
	.miniflag {
		font-family: var(--font-sans);
		font-size: 9px;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		padding: 2px 6px;
		background: var(--card-2);
		border: 1px solid var(--primary-300);
		color: var(--mut);
		white-space: nowrap;
	}
	.wg {
		font-size: 13px;
		color: var(--ink-2);
	}
	.rowlink:hover td.name {
		color: var(--brand-strong);
	}
	code {
		font-family: var(--font-sans);
		background: var(--card-2);
		padding: 2px 6px;
		border: 1px solid var(--primary-300);
		font-size: 13px;
	}
</style>
