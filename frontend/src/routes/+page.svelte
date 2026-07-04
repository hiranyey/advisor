<script>
	import { goto } from '$app/navigation';
	import { fly } from 'svelte/transition';
	import { api, inr, pct, catLabel, catColor, fmtDate } from '$lib/api.js';
	import AllocationBreakdown from '$lib/components/AllocationBreakdown.svelte';
	import RiskPill from '$lib/components/RiskPill.svelte';
	import { Radar, Layers, Target } from '@lucide/svelte';

	let radar = $state(null);
	let summary = $state(null);
	let loading = $state(true);
	let error = $state(null);

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
	}

	const rise = (i) => ({ y: 10, duration: 240, delay: i * 55 });

	// Heatmap cell state → design-system severity class + plain-language label.
	const CELL = {
		ok: { cls: 'ok', label: 'Fine' },
		tight: { cls: 'low', label: 'At limit' },
		breach: { cls: 'bad', label: 'Too risky' }
	};
	// Call-list status → severity class + plain-language action.
	const STATUS = {
		breach: { cls: 'crit', label: 'call now' },
		watch: { cls: 'warn', label: 'keep an eye' },
		ok: { cls: 'good', label: 'fine' }
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
</script>

<div class="page">
	<p class="eyebrow">Book Analytics</p>
	<div class="titlewrap">
		<span class="titleicon"><Radar size={46} strokeWidth={1.5} /></span>
		<h1 class="title">Full-book risk radar</h1>
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

		<!-- KPI strip -->
		<div class="kpis">
			<div class="kpi crit">
				<div class="lab">Too Much Risk</div>
				<div class="val crit num">{radar.kpis.mismatches}</div>
				<div class="foot">clients whose portfolio is riskier than they're comfortable with</div>
			</div>
			<div class="kpi warn">
				<div class="lab">Behind on Goals</div>
				<div class="val warn num">{radar.kpis.off_track_clients}</div>
				<div class="foot">have a goal likely to fall short · {radar.kpis.watch} more close to the line</div>
			</div>
			<div class="kpi good">
				<div class="lab">Typical Goal Chance</div>
				<div class="val good num">{pct(radar.kpis.median_goal_success)}</div>
				<div class="foot">how likely an average goal is to be reached on plan</div>
			</div>
			<div class="kpi ink">
				<div class="lab">A Bad Year Could Cost</div>
				<div class="val num">{neg(radar.kpis.book_var_95)}</div>
				<div class="foot">the typical portfolio in a rough year · severe cases near {neg(radar.kpis.book_cvar_95)}</div>
			</div>
		</div>

		<div class="grid">
			<!-- Risk vs. comfort heatmap -->
			<div class="card" in:fly={rise(0)}>
				<h2>Risk vs. comfort</h2>
				<p class="h2sub">
					Down: how much risk each client says they're OK with. Across: how much their portfolio
					could actually drop in a bad year. Rose cells mean the drop is bigger than they'd be
					comfortable with.
				</p>
				<div class="hm">
					<div class="corner">comfortable with ↓ / could drop →</div>
					{#each radar.heatmap_columns as col}
						<div class="colh">{col}</div>
					{/each}
					{#each radar.heatmap as row}
						<div class="rowh">
							{cap(row.profile)}<small>OK down to −{pct(row.tolerable_dd)}</small>
						</div>
						{#each row.cells as cell}
							<div class="hc {CELL[cell.state].cls}">
								<div class="c num">{cell.count}</div>
								<div class="t">{CELL[cell.state].label}</div>
							</div>
						{/each}
					{/each}
				</div>
				<div class="legend">
					<span><i style="background:rgba(74,124,58,.5)"></i>Comfortable</span>
					<span><i style="background:rgba(184,134,11,.6)"></i>At their limit</span>
					<span><i style="background:rgba(168,34,74,.6)"></i>Riskier than they want — call them</span>
				</div>
			</div>

			<!-- Goal-success distribution -->
			<div class="card" in:fly={rise(1)}>
				<h2>How likely are goals to be met?</h2>
				<p class="h2sub">
					Every client goal, grouped by its chance of being reached on plan. The red bars on top
					are goals that are behind and need attention.
				</p>
				<div class="hist">
					{#each radar.goal_success_hist as b, i}
						<div class="histrow">
							<span class="hlab num">{b.label}</span>
							<span class="hbar">
								<span
									class="hfill"
									style="width:{(b.count / histMax) * 100}%;background:{HIST_COLORS[i]}"
								></span>
							</span>
							<span class="hcount num">{b.count}</span>
						</div>
					{/each}
				</div>
				<p class="mini" style="margin-top:14px">
					<Target size={13} strokeWidth={1.9} style="vertical-align:-2px" />
					A typical goal has a <b>{pct(radar.kpis.median_goal_success)}</b> chance of being met —
					most clients are partway to their targets, not there yet.
				</p>
			</div>

			<!-- Priority call list -->
			<div class="card full" in:fly={rise(2)}>
				<h2>Who to call first</h2>
				<p class="h2sub">
					Clients whose portfolio is riskier than they're comfortable with — most urgent at the
					top. Click a row to open the client.
				</p>
				<div class="h2rule"></div>
				<div class="tablescroll">
					<table>
						<thead>
							<tr>
								<th>#</th>
								<th>Client</th>
								<th>Risk they want</th>
								<th class="r">OK to drop</th>
								<th class="r">Could drop</th>
								<th class="r">Over by</th>
								<th>Main reason</th>
								<th>Goal at risk</th>
								<th>Action</th>
							</tr>
						</thead>
						<tbody>
							{#each radar.call_list as c, i}
								<tr class="rowlink" onclick={() => goto(`/clients/${c.client_id}`)}>
									<td class="num dim">{i + 1}</td>
									<td class="name">{c.name}</td>
									<td><RiskPill profile={c.risk_profile} /></td>
									<td class="r num">{neg(c.tolerable_dd)}</td>
									<td class="r num">{neg(c.simulated_dd)}</td>
									<td class="r">
										<span class="gap" class:pos={c.mismatch > 0}>
											{c.mismatch > 0 ? '+' : ''}{(c.mismatch * 100).toFixed(0)}%
										</span>
									</td>
									<td>
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
									</td>
									<td>
										{#if c.worst_goal_name}
											<span class="wg">{c.worst_goal_name}</span>
											<span class="dim num"> {pct(c.worst_goal_prob)}</span>
										{:else}
											<span class="dim">—</span>
										{/if}
									</td>
									<td><span class="pill {STATUS[c.status].cls}">{STATUS[c.status].label}</span></td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>

			<!-- Book-wide allocation (context) -->
			{#if summary}
				<div class="card full" in:fly={rise(3)}>
					<h2>Book-wide allocation</h2>
					<p class="h2sub">
						Every client's holdings rolled up by category — the 14 "asset classes" the engine
						simulates. Book AUM {inr(summary.total_aum)} across {summary.total_clients} clients.
					</p>
					<div class="h2rule"></div>
					<AllocationBreakdown allocation={summary.allocation} />
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
	/* Heatmap */
	.hm {
		display: grid;
		grid-template-columns: 128px repeat(4, 1fr);
		gap: 6px;
		align-items: stretch;
	}
	.hm .corner {
		font-size: 10px;
		color: var(--faint);
		align-self: end;
	}
	.hm .colh {
		font-family: var(--font-sans);
		font-size: 9.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		font-weight: 600;
		color: var(--mut);
		text-align: center;
		padding-bottom: 4px;
	}
	.hm .rowh {
		font-size: 13px;
		color: var(--ink);
		display: flex;
		flex-direction: column;
		justify-content: center;
		font-weight: 500;
	}
	.hm .rowh small {
		color: var(--faint);
		font-size: 10px;
	}
	.hc {
		padding: 14px 6px;
		text-align: center;
		font-family: var(--font-serif);
	}
	.hc .c {
		font-size: 22px;
		font-weight: 800;
	}
	.hc .t {
		font-family: var(--font-sans);
		font-size: 8.5px;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		font-weight: 600;
		opacity: 0.8;
	}
	.ok {
		background: rgba(74, 124, 58, 0.14);
		color: var(--inflow);
	}
	.low {
		background: rgba(184, 134, 11, 0.16);
		color: var(--primary-800);
	}
	.bad {
		background: rgba(168, 34, 74, 0.13);
		color: var(--outflow);
		box-shadow: 0 0 0 1.5px rgba(168, 34, 74, 0.42) inset;
	}
	.legend {
		display: flex;
		gap: 16px;
		margin-top: 16px;
		font-size: 11.5px;
		color: var(--mut);
		flex-wrap: wrap;
		font-weight: 500;
	}
	.legend i {
		width: 12px;
		height: 12px;
		display: inline-block;
		vertical-align: -1px;
		margin-right: 6px;
	}

	/* Goal-success histogram */
	.hist {
		display: flex;
		flex-direction: column;
		gap: 9px;
	}
	.histrow {
		display: grid;
		grid-template-columns: 62px 1fr 34px;
		align-items: center;
		gap: 10px;
	}
	.hlab {
		font-size: 11px;
		color: var(--mut);
		text-align: right;
		font-weight: 600;
	}
	.hbar {
		height: 18px;
		background: var(--primary-200);
		border: 1px solid var(--primary-300);
		display: block;
		overflow: hidden;
	}
	.hfill {
		display: block;
		height: 100%;
		transform-origin: left;
		animation: growbar 620ms var(--ease-out) both;
	}
	.hcount {
		font-size: 13px;
		font-weight: 700;
		color: var(--ink);
		text-align: right;
	}
	@keyframes growbar {
		from {
			transform: scaleX(0);
		}
		to {
			transform: scaleX(1);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.hfill {
			animation: none;
		}
	}

	/* Call list */
	.tablescroll {
		overflow-x: auto;
	}
	.gap {
		font-family: var(--font-serif);
		font-weight: 800;
		font-variant-numeric: tabular-nums;
		color: var(--faint);
		font-size: 16px;
	}
	.gap.pos {
		color: var(--outflow);
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
