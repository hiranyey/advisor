<script>
	import { api, inr, inrFull, catLabel, catColor } from '$lib/api.js';
	import AllocationBreakdown from '$lib/components/AllocationBreakdown.svelte';
	import RiskPill from '$lib/components/RiskPill.svelte';
	import { Radar } from '@lucide/svelte';

	let summary = $state(null);
	let clients = $state([]);
	let loading = $state(true);
	let error = $state(null);

	$effect(() => {
		load();
	});

	async function load() {
		loading = true;
		error = null;
		try {
			[summary, clients] = await Promise.all([api.bookSummary(), api.listClients({ limit: 500 })]);
		} catch (e) {
			error = e.message;
		} finally {
			loading = false;
		}
	}

	const topClients = $derived(clients.slice(0, 10));
</script>

<div class="page">
	<p class="eyebrow">Book Analytics</p>
	<div class="titlewrap">
		<span class="titleicon"><Radar size={46} strokeWidth={1.5} /></span>
		<h1 class="title">Full-book risk radar</h1>
	</div>
	<div class="titlerule"></div>

	{#if loading}
		<div class="loading">Loading book…</div>
	{:else if error}
		<div class="errbox">Couldn't reach the API — {error}</div>
	{:else if summary}
		<p class="runline">
			<span class="dot"></span>
			<span>Live from ledger</span> · <b>{summary.total_clients} clients</b> ·
			<b>{summary.total_goals} goals</b> · book AUM
			<b>{inr(summary.total_aum)}</b> · avg age {summary.avg_age?.toFixed(0)}
			<span class="slow">(suitability sim — Monte Carlo — comes online next)</span>
		</p>

		<div class="kpis">
			<div class="kpi ink">
				<div class="lab">Assets Under Advice</div>
				<div class="val num">{inr(summary.total_aum)}</div>
				<div class="foot">across {summary.total_clients} clients</div>
			</div>
			<div class="kpi good">
				<div class="lab">Conservative</div>
				<div class="val good num">{summary.by_risk_profile.conservative}</div>
				<div class="foot">capital-preservation profile</div>
			</div>
			<div class="kpi warn">
				<div class="lab">Balanced</div>
				<div class="val warn num">{summary.by_risk_profile.balanced}</div>
				<div class="foot">moderate risk tolerance</div>
			</div>
			<div class="kpi crit">
				<div class="lab">Aggressive</div>
				<div class="val crit num">{summary.by_risk_profile.aggressive}</div>
				<div class="foot">high risk tolerance</div>
			</div>
		</div>

		<div class="grid">
			<div class="card full">
				<h2>Largest books</h2>
				<p class="h2sub">Top clients by portfolio value. Row links into the client detail.</p>
				<div class="h2rule"></div>
				<table>
					<thead>
						<tr>
							<th>#</th>
							<th>Client</th>
							<th>Profile</th>
							<th class="r">Age</th>
							<th class="r">Goals</th>
							<th class="r">Portfolio</th>
						</tr>
					</thead>
					<tbody>
						{#each topClients as c, i}
							<tr class="rowlink" onclick={() => (window.location.href = `/clients/${c.id}`)}>
								<td class="num">{i + 1}</td>
								<td class="name">{c.name}</td>
								<td><RiskPill profile={c.risk_profile} /></td>
								<td class="r num">{c.age ?? '—'}</td>
								<td class="r num">{c.goal_count}</td>
								<td class="r num">{inr(c.portfolio_value)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>

			<div class="card full">
				<h2>Book-wide allocation</h2>
				<p class="h2sub">
					Every client's holdings rolled up by category — the 14 "asset classes" the engine
					simulates.
				</p>
				<div class="h2rule"></div>
				<AllocationBreakdown allocation={summary.allocation} />
			</div>
		</div>
	{/if}
</div>
