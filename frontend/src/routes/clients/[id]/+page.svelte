<script>
	import { page } from '$app/state';
	import { fly } from 'svelte/transition';
	import { api, inr, inrFull, pct, catLabel, catColor, fmtDate } from '$lib/api.js';
	import { goalIcon, catIcon } from '$lib/icons.js';
	import AllocationBreakdown from '$lib/components/AllocationBreakdown.svelte';
	import ValueChart from '$lib/components/ValueChart.svelte';
	import RiskPill from '$lib/components/RiskPill.svelte';
	import { ChevronLeft, TriangleAlert, User, Repeat, TrendingUp, ArrowDownRight, ArrowUpRight } from '@lucide/svelte';

	// Quick, non-blocking mount fade — cards rise in with a light stagger.
	const rise = (i) => ({ y: 10, duration: 240, delay: i * 55 });

	const id = $derived(page.params.id);

	let client = $state(null);
	let holdings = $state(null);
	let sipData = $state(null);
	let txnData = $state(null);
	let loading = $state(true);
	let error = $state(null);

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
	}

	const flagLabel = {
		concentrated_fund: 'Single fund > 25%',
		concentrated_category: 'Single category > 40%'
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
		</div>
		<div class="titlerule"></div>
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

			<!-- Goals -->
			<div class="card full" in:fly={rise(2)}>
				<h2>Goals</h2>
				{#if client.goals.length === 0}
					<p class="dim">No goals recorded.</p>
				{:else}
					<div class="goals">
						{#each client.goals as g}
							{@const prog = g.target_amount ? Math.min(g.funded_value / g.target_amount, 1) : 0}
							{@const GIcon = goalIcon(g.name)}
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
							</div>
						{/each}
					</div>
				{/if}
			</div>

			<!-- Holdings table -->
			<div class="card full" in:fly={rise(3)}>
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
			<div class="card full" in:fly={rise(4)}>
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
			<div class="card full" in:fly={rise(5)}>
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
</style>
