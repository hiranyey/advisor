<script>
	import { page } from '$app/state';
	import { fly } from 'svelte/transition';
	import { api, inr, inrFull, pct, catLabel, catColor, fmtDate } from '$lib/api.js';
	import { goalIcon, catIcon } from '$lib/icons.js';
	import AllocationBreakdown from '$lib/components/AllocationBreakdown.svelte';
	import ValueChart from '$lib/components/ValueChart.svelte';
	import RiskPill from '$lib/components/RiskPill.svelte';
	import ShareDebriefModal from '$lib/components/ShareDebriefModal.svelte';
	import { ChevronLeft, TriangleAlert, User, Repeat, TrendingUp, ArrowDownRight, ArrowUpRight, Activity, ShieldAlert, Cpu, Gauge, Target, Share2 } from '@lucide/svelte';

	// Quick, non-blocking mount fade — cards rise in with a light stagger.
	const rise = (i) => ({ y: 10, duration: 240, delay: i * 55 });

	const id = $derived(page.params.id);

	let client = $state(null);
	let holdings = $state(null);
	let sipData = $state(null);
	let txnData = $state(null);
	let loading = $state(true);
	let error = $state(null);

	// Live Monte Carlo insights — a ~1s single-client re-sim, loaded separately so it
	// never blocks the ledger/holdings render.
	let insights = $state(null);
	let insightsError = $state(null);

	let shareOpen = $state(false);

	$effect(() => {
		load(id);
	});

	async function load(cid) {
		loading = true;
		error = null;
		client = null;
		holdings = null;
		sipData = null;
		txnData = null;
		insights = null;
		insightsError = null;
		try {
			[client, holdings, sipData, txnData] = await Promise.all([
				api.getClient(cid),
				api.getHoldings(cid),
				api.getSips(cid),
				api.getTransactions(cid)
			]);
		} catch (e) {
			error = e.message;
		} finally {
			loading = false;
		}
		// Fire the simulation independently — goal cards + risk panel fill in when ready.
		api
			.getInsights(cid)
			.then((r) => (insights = r))
			.catch((e) => (insightsError = e.message));
	}

	// goal_id → its simulated insight, for merging success onto the goal cards.
	const goalInsight = $derived(
		Object.fromEntries((insights?.goals ?? []).map((g) => [g.goal_id, g]))
	);

	// Tint a success probability: green on-track, amber close, red off-track.
	function probClass(p) {
		if (p == null) return '';
		if (p >= 0.8) return 'good';
		if (p >= 0.5) return 'watch';
		return 'crit';
	}

	const flagLabel = {
		concentrated_fund: 'Single fund > 25%',
		concentrated_category: 'Single category > 40%',
		off_track: 'Goal off track'
	};
</script>

<div class="page">
	<p class="eyebrow"><a href="/clients" class="back"><ChevronLeft size={13} strokeWidth={2.2} /> Clients</a></p>

	{#if loading}
		<div class="loading">Loading client…</div>
	{:else if error}
		<div class="errbox">Couldn't reach the API — {error}</div>
	{:else if client}
		<div class="titlewrap">
			<span class="titleicon"><User size={44} strokeWidth={1.5} /></span>
			<h1 class="title">{client.name}</h1>
			<button class="sharebtn" onclick={() => (shareOpen = true)}>
				<Share2 size={14} strokeWidth={2} /> Share
			</button>
		</div>
		<div class="titlerule"></div>

		{#if shareOpen}
			<ShareDebriefModal clientId={id} onClose={() => (shareOpen = false)} />
		{/if}
		<p class="runline">
			<RiskPill profile={client.risk_profile} />
			<span>· Age {client.age ?? '—'}</span>
			<span>· Portfolio <b>{inr(client.portfolio_value)}</b></span>
			<span>· {client.goals.length} goal{client.goals.length === 1 ? '' : 's'}</span>
			{#each holdings?.flags ?? [] as f}
				<span class="pill pillicon crit"><TriangleAlert size={12} strokeWidth={2} />{flagLabel[f] ?? f}</span>
			{/each}
		</p>

		<div class="grid">
			<!-- Value over time -->
			<div class="card" in:fly={rise(0)}>
				<h2>Portfolio value over time</h2>
				<ValueChart points={holdings?.value_over_time ?? []} />
			</div>

			<!-- Allocation -->
			<div class="card" in:fly={rise(1)}>
				<h2>Category allocation</h2>
				<AllocationBreakdown allocation={holdings?.allocation ?? []} />
			</div>

			<!-- Monte Carlo risk analysis (live single-client re-sim) -->
			<div class="card full" in:fly={rise(2)}>
				<div class="siphead">
					<h2><span class="h2icon"><Activity size={16} strokeWidth={2} /></span>Risk analysis</h2>
					{#if insights}
						<span class="simbadge" title="Backend + wall-time for this re-simulation">
							<Cpu size={13} strokeWidth={2} />
							{insights.backend} · {insights.n_paths.toLocaleString('en-IN')} paths ·
							<b>{insights.elapsed_ms} ms</b>
						</span>
					{/if}
				</div>

				{#if insightsError}
					<p class="dim">Couldn't run the simulation — {insightsError}</p>
				{:else if !insights}
					<p class="dim simloading"><Activity size={14} strokeWidth={2} /> Simulating futures…</p>
				{:else}
					<div class="verdict {insights.over_exposed ? 'crit' : 'good'}">
						{#if insights.over_exposed}
							<ShieldAlert size={16} strokeWidth={2} />
							<span
								>This portfolio is riskier than it should be. In a bad year, it could lose
								<b>{pct(insights.max_drawdown)}</b> of its value — more than the
								{pct(insights.tolerable_dd)} that's considered safe for a {client.risk_profile} investor
								(by {pct(insights.suitability_mismatch)} too much).</span
							>
						{:else}
							<ShieldAlert size={16} strokeWidth={2} />
							<span
								>This portfolio's risk level looks fine. In a bad year, it could lose
								<b>{pct(insights.max_drawdown)}</b> of its value, which is within the
								{pct(insights.tolerable_dd)} considered safe for a {client.risk_profile} investor.</span
							>
						{/if}
					</div>

					<div class="stats">
						<div class="stat">
							<span class="slabel"><Gauge size={13} strokeWidth={2} /> Biggest possible drop</span>
							<span class="sval {probClass(1 - insights.max_drawdown / (insights.tolerable_dd || 1))}"
								>−{pct(insights.max_drawdown)}</span
							>
							<span class="ssub">safe limit is −{pct(insights.tolerable_dd)}</span>
						</div>
						<div class="stat">
							<span class="slabel">Loss in a bad year</span>
							<span class="sval">−{pct(insights.var_95)}</span>
							<span class="ssub">happens about 1 year in 20</span>
						</div>
						<div class="stat">
							<span class="slabel">Loss in a very bad year</span>
							<span class="sval">−{pct(insights.cvar_95)}</span>
							<span class="ssub">average of the worst outcomes</span>
						</div>
						<div class="stat">
							<span class="slabel">Risk score</span>
							<span class="sval">{insights.risk_score}<span class="ssuffix">/100</span></span>
							<span class="ssub">higher = riskier</span>
						</div>
					</div>
				{/if}
			</div>

			<!-- Goals -->
			<div class="card full" in:fly={rise(3)}>
				<h2>Goals</h2>
				{#if client.goals.length === 0}
					<p class="dim">No goals recorded.</p>
				{:else}
					<div class="goals">
						{#each client.goals as g}
							{@const prog = g.target_amount ? Math.min(g.funded_value / g.target_amount, 1) : 0}
							{@const GIcon = goalIcon(g.name)}
							{@const gi = goalInsight[g.id]}
							<div class="goal">
								<div class="goalhead">
									<span class="gname"><span class="gicon"><GIcon size={17} strokeWidth={1.8} /></span>{g.name ?? 'Goal'}</span>
									<span class="dim">by {fmtDate(g.target_date)}</span>
								</div>
								<div class="track">
									<div
										class="fill"
										class:done={prog >= 1}
										style="width:{prog * 100}%"
									></div>
								</div>
								<div class="goalfoot">
									<span class="num">{inr(g.funded_value)} funded</span>
									<span class="dim num">of {inr(g.target_amount)} · {pct(prog)}</span>
								</div>

								<!-- Monte Carlo success probability + get-on-track SIP -->
								<div class="goalmc">
									{#if gi}
										<div class="probrow">
											<span class="problabel"><Target size={12} strokeWidth={2.2} /> Chance of hitting target</span>
											<span class="probval {probClass(gi.success_prob)}">{pct(gi.success_prob)}</span>
										</div>
										<div class="probbar">
											<div class="probfill {probClass(gi.success_prob)}" style="width:{gi.success_prob * 100}%"></div>
											<div class="probmark" style="left:{insights.confidence * 100}%" title="Target confidence {pct(insights.confidence)}"></div>
										</div>
										{#if gi.on_track}
											<p class="mcnote good">On track · median outcome {inr(gi.p50)}</p>
										{:else}
											<p class="mcnote crit">
												Off track ·
												{#if gi.required_sip}
													lift SIP to <b>{inr(gi.required_sip)}/mo</b>
													{#if gi.current_sip}<span class="dim">(from {inr(gi.current_sip)})</span>{/if}
													for {pct(insights.confidence)} confidence
												{:else}
													~{inr(gi.shortfall_expected)} short on average
												{/if}
											</p>
										{/if}
									{:else if insightsError}
										<p class="mcnote dim">Simulation unavailable</p>
									{:else}
										<p class="mcnote dim skeleton">Simulating…</p>
									{/if}
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</div>

			<!-- Holdings table -->
			<div class="card full" in:fly={rise(4)}>
				<h2>Holdings</h2>
				<table>
					<thead>
						<tr>
							<th>Fund</th>
							<th>AMC</th>
							<th>Category</th>
							<th class="r">Units</th>
							<th class="r">NAV</th>
							<th class="r">Value</th>
							<th class="r">Weight</th>
						</tr>
					</thead>
					<tbody>
						{#each holdings?.holdings ?? [] as h}
							{@const CIcon = catIcon(h.category)}
							<tr>
								<td class="name">{h.fund_name}</td>
								<td class="dim">{h.amc ?? '—'}</td>
								<td>
									<span class="catcell">
										<span class="cicon" style="color:{catColor(h.category)}"><CIcon size={15} strokeWidth={1.9} /></span>
										{catLabel(h.category)}
									</span>
								</td>
								<td class="r num">{h.units.toLocaleString('en-IN', { maximumFractionDigits: 1 })}</td>
								<td class="r num">₹{h.nav.toFixed(2)}</td>
								<td class="r num">{inr(h.value)}</td>
								<td class="r num" class:hot={h.weight > 0.25}>{pct(h.weight, 1)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>

			<!-- SIP schedule -->
			<div class="card full" in:fly={rise(5)}>
				<div class="siphead">
					<h2>SIP schedule</h2>
					{#if sipData && sipData.sips.length}
						<span class="siptotal"
							><Repeat size={15} strokeWidth={1.9} /> {inr(sipData.total_monthly)}
							<span class="dim">/ month active</span></span
						>
					{/if}
				</div>
				{#if !sipData || sipData.sips.length === 0}
					<p class="dim">No SIPs set up for this client.</p>
				{:else}
					<table>
						<thead>
							<tr>
								<th>Fund</th>
								<th>Category</th>
								<th class="r">Monthly</th>
								<th class="r">Step-up</th>
								<th>Since</th>
								<th>Status</th>
							</tr>
						</thead>
						<tbody>
							{#each sipData.sips as s}
								{@const SIcon = catIcon(s.category)}
								<tr class:inactive={!s.active}>
									<td class="name">{s.fund_name}</td>
									<td>
										<span class="catcell">
											<span class="cicon" style="color:{catColor(s.category)}"><SIcon size={15} strokeWidth={1.9} /></span>
											{catLabel(s.category)}
										</span>
									</td>
									<td class="r num">{inr(s.monthly_amount)}</td>
									<td class="r num">
										{#if s.stepup_rate > 0}
											<span class="stepup"><TrendingUp size={12} strokeWidth={2} />{pct(s.stepup_rate)}/yr</span>
										{:else}
											<span class="dim">—</span>
										{/if}
									</td>
									<td class="dim">{fmtDate(s.start_date)}</td>
									<td>
										<span class="pill {s.active ? 'good' : 'neutral'}">{s.active ? 'active' : 'paused'}</span>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				{/if}
			</div>

			<!-- Transaction ledger -->
			<div class="card full" in:fly={rise(6)}>
				<div class="siphead">
					<h2>Transactions</h2>
					{#if txnData && txnData.transactions.length}
						<span class="dim txncount">{txnData.transactions.length} recorded</span>
					{/if}
				</div>
				{#if !txnData || txnData.transactions.length === 0}
					<p class="dim">No transactions recorded for this client.</p>
				{:else}
					<table>
						<thead>
							<tr>
								<th>Date</th>
								<th>Fund</th>
								<th>Category</th>
								<th>Type</th>
								<th class="r">Units</th>
								<th class="r">NAV</th>
								<th class="r">Amount</th>
							</tr>
						</thead>
						<tbody>
							{#each txnData.transactions as t}
								{@const TIcon = catIcon(t.category)}
								<tr>
									<td class="dim">{fmtDate(t.date)}</td>
									<td class="name">{t.fund_name}</td>
									<td>
										<span class="catcell">
											<span class="cicon" style="color:{catColor(t.category)}"><TIcon size={15} strokeWidth={1.9} /></span>
											{catLabel(t.category)}
										</span>
									</td>
									<td>
										<span class="txntype {t.type === 'buy' ? 'buy' : 'redeem'}">
											{#if t.type === 'buy'}
												<ArrowDownRight size={12} strokeWidth={2.2} />
											{:else}
												<ArrowUpRight size={12} strokeWidth={2.2} />
											{/if}
											{t.type}
										</span>
									</td>
									<td class="r num">{t.units.toLocaleString('en-IN', { maximumFractionDigits: 1 })}</td>
									<td class="r num">₹{t.nav.toFixed(2)}</td>
									<td class="r num">{inr(t.amount)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				{/if}
			</div>
		</div>
	{/if}
</div>

<style>
	.sharebtn {
		margin-left: auto;
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
	}
	.sharebtn:hover {
		transform: translate(-1px, -1px);
		box-shadow: var(--shadow-stamp);
	}
	.back {
		text-decoration: none;
		display: inline-flex;
		align-items: center;
		gap: 3px;
		transition:
			gap 140ms var(--ease-out),
			color 140ms ease;
	}
	.back:hover {
		gap: 7px;
		color: var(--brand-strong);
	}
	.gname {
		display: inline-flex;
		align-items: center;
		gap: 8px;
	}
	.gicon {
		color: var(--brand);
		display: flex;
		transition: transform 160ms var(--ease-out);
	}
	.catcell {
		display: inline-flex;
		align-items: center;
		gap: 8px;
	}
	.siphead {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 12px;
		flex-wrap: wrap;
	}
	.siptotal {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-family: var(--font-serif);
		font-weight: 700;
		font-size: 18px;
		color: var(--inflow);
		font-variant-numeric: tabular-nums;
	}
	.siptotal .dim {
		font-family: var(--font-sans);
		font-weight: 400;
	}
	.stepup {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		color: var(--inflow);
		font-weight: 600;
		justify-content: flex-end;
	}
	tr.inactive td.name,
	tr.inactive .catcell {
		opacity: 0.55;
	}
	.cicon {
		display: flex;
		flex: none;
	}
	.goals {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
		gap: 20px;
	}
	.goal {
		border: 1px solid var(--rule);
		background: var(--card);
		padding: 14px 16px;
		cursor: default;
		transition:
			transform 170ms var(--ease-out),
			border-color 170ms ease,
			box-shadow 170ms var(--ease-out);
	}
	.goal:hover {
		transform: translateY(-3px) scale(1.015);
		border-color: var(--primary-800);
		box-shadow: var(--shadow-stamp-sm);
	}
	.goal:hover .gicon {
		transform: scale(1.25);
	}
	.goal:hover .gname {
		color: var(--brand-strong);
	}
	.goal:hover .fill {
		filter: brightness(1.09);
	}
	.goalhead {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		margin-bottom: 8px;
	}
	.gname {
		font-family: var(--font-serif);
		font-weight: 700;
		font-size: 16px;
		transition: color 140ms ease;
	}
	.track {
		height: 10px;
		background: var(--primary-200);
		border: 1px solid var(--primary-300);
		overflow: hidden;
	}
	.fill {
		height: 100%;
		background: var(--primary-600);
		transform-origin: left;
		transition: filter 150ms ease;
		animation: growbar 620ms var(--ease-out) both;
	}
	.fill.done {
		background: var(--inflow);
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
		.goal,
		.gicon,
		.gname,
		.fill {
			transition: none;
			animation: none;
		}
		.goal:hover {
			transform: none;
		}
	}
	.goalfoot {
		display: flex;
		justify-content: space-between;
		margin-top: 7px;
		font-size: 13px;
	}
	td.hot {
		color: var(--outflow);
		font-weight: 700;
	}
	.txncount {
		font-size: 13px;
	}
	.txntype {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		font-weight: 600;
		text-transform: capitalize;
	}
	.txntype.buy {
		color: var(--inflow);
	}
	.txntype.redeem {
		color: var(--outflow);
	}

	/* ── Monte Carlo risk analysis card ── */
	.h2icon {
		display: inline-flex;
		vertical-align: -2px;
		margin-right: 6px;
		color: var(--brand);
	}
	.simbadge {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		font-size: 12px;
		color: var(--ink-soft, var(--muted, #6b6b6b));
		font-variant-numeric: tabular-nums;
		border: 1px solid var(--rule);
		padding: 3px 8px;
		border-radius: 3px;
		background: var(--card);
	}
	.simbadge b {
		color: var(--brand-strong);
	}
	.simloading {
		display: inline-flex;
		align-items: center;
		gap: 6px;
	}
	.simloading :global(svg) {
		animation: pulse 1.1s ease-in-out infinite;
	}
	@keyframes pulse {
		0%, 100% { opacity: 0.4; }
		50% { opacity: 1; }
	}
	.verdict {
		display: flex;
		align-items: flex-start;
		gap: 9px;
		padding: 11px 13px;
		border: 1px solid var(--rule);
		border-left-width: 3px;
		background: var(--card);
		font-size: 14px;
		line-height: 1.45;
		margin-bottom: 16px;
	}
	.verdict :global(svg) {
		flex: none;
		margin-top: 2px;
	}
	.verdict.crit {
		border-left-color: var(--outflow);
		color: var(--outflow);
	}
	.verdict.good {
		border-left-color: var(--inflow);
		color: var(--inflow);
	}
	.verdict b {
		font-weight: 700;
	}
	.stats {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
		gap: 14px;
	}
	.stat {
		display: flex;
		flex-direction: column;
		gap: 3px;
		border: 1px solid var(--rule);
		padding: 12px 14px;
		background: var(--card);
	}
	.slabel {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		font-size: 12px;
		color: var(--muted, #6b6b6b);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.sval {
		font-family: var(--font-serif);
		font-weight: 700;
		font-size: 26px;
		font-variant-numeric: tabular-nums;
		line-height: 1.1;
	}
	.sval.good { color: var(--inflow); }
	.sval.watch { color: var(--warn, #b8860b); }
	.sval.crit { color: var(--outflow); }
	.ssuffix {
		font-size: 15px;
		font-weight: 400;
		color: var(--muted, #6b6b6b);
	}
	.ssub {
		font-size: 12px;
		color: var(--muted, #6b6b6b);
	}

	/* ── Per-goal Monte Carlo block ── */
	.goalmc {
		margin-top: 12px;
		padding-top: 11px;
		border-top: 1px dashed var(--rule);
	}
	.probrow {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		margin-bottom: 5px;
	}
	.problabel {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		font-size: 12px;
		color: var(--muted, #6b6b6b);
	}
	.probval {
		font-family: var(--font-serif);
		font-weight: 700;
		font-size: 15px;
		font-variant-numeric: tabular-nums;
	}
	.probval.good { color: var(--inflow); }
	.probval.watch { color: var(--warn, #b8860b); }
	.probval.crit { color: var(--outflow); }
	.probbar {
		position: relative;
		height: 7px;
		background: var(--primary-200);
		border: 1px solid var(--primary-300);
		overflow: hidden;
	}
	.probfill {
		height: 100%;
		transform-origin: left;
		animation: growbar 620ms var(--ease-out) both;
	}
	.probfill.good { background: var(--inflow); }
	.probfill.watch { background: var(--warn, #b8860b); }
	.probfill.crit { background: var(--outflow); }
	.probmark {
		position: absolute;
		top: -2px;
		bottom: -2px;
		width: 2px;
		background: var(--ink, #333);
		opacity: 0.55;
	}
	.mcnote {
		font-size: 12.5px;
		margin-top: 6px;
	}
	.mcnote.good { color: var(--inflow); }
	.mcnote.crit { color: var(--outflow); }
	.mcnote b { font-weight: 700; }
	.skeleton {
		opacity: 0.6;
		animation: pulse 1.1s ease-in-out infinite;
	}
	@media (prefers-reduced-motion: reduce) {
		.simloading :global(svg),
		.probfill,
		.skeleton {
			animation: none;
		}
	}
</style>
