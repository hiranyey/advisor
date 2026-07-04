<script>
	import { goto } from '$app/navigation';
	import { fly } from 'svelte/transition';
	import { api, inr } from '$lib/api.js';
	import RiskPill from '$lib/components/RiskPill.svelte';
	import { Users, Search, ChevronUp, ChevronDown, ChevronsUpDown } from '@lucide/svelte';

	let clients = $state([]);
	let loading = $state(true);
	let error = $state(null);

	let q = $state('');
	let riskFilter = $state('');

	// Client-side sorting. Numeric columns default to desc, text to asc.
	let sortKey = $state('portfolio_value');
	let sortDir = $state('desc');
	const NUMERIC = new Set(['age', 'goal_count', 'fund_count', 'portfolio_value']);

	function sortBy(key) {
		if (sortKey === key) {
			sortDir = sortDir === 'asc' ? 'desc' : 'asc';
		} else {
			sortKey = key;
			sortDir = NUMERIC.has(key) ? 'desc' : 'asc';
		}
	}

	const sorted = $derived.by(() => {
		const arr = [...clients];
		arr.sort((a, b) => {
			let x = a[sortKey];
			let y = b[sortKey];
			let cmp;
			if (NUMERIC.has(sortKey)) {
				cmp = (x ?? 0) - (y ?? 0);
			} else {
				cmp = String(x ?? '').localeCompare(String(y ?? ''));
			}
			return sortDir === 'asc' ? cmp : -cmp;
		});
		return arr;
	});

	$effect(() => {
		load();
	});

	async function load() {
		loading = true;
		error = null;
		try {
			clients = await api.listClients({ q, risk_profile: riskFilter, limit: 500 });
		} catch (e) {
			error = e.message;
		} finally {
			loading = false;
		}
	}

	// debounce search / filter
	let timer;
	function onFilter() {
		clearTimeout(timer);
		timer = setTimeout(load, 250);
	}
</script>

{#snippet sortHead(key, label, right = false)}
	<th class="sortable" class:r={right} class:active={sortKey === key} onclick={() => sortBy(key)}>
		<span class="thlabel">
			{label}
			{#if sortKey === key}
				{#if sortDir === 'asc'}<ChevronUp size={13} strokeWidth={2.4} />{:else}<ChevronDown
						size={13}
						strokeWidth={2.4}
					/>{/if}
			{:else}
				<span class="sortidle"><ChevronsUpDown size={13} strokeWidth={2} /></span>
			{/if}
		</span>
	</th>
{/snippet}

<div class="page">
	<p class="eyebrow">Client Book</p>
	<div class="titlewrap">
		<span class="titleicon"><Users size={46} strokeWidth={1.5} /></span>
		<h1 class="title">Clients</h1>
	</div>
	<div class="titlerule"></div>

	<div class="controls">
		<div class="searchbox">
			<Search size={16} strokeWidth={1.8} />
			<input class="search" placeholder="Search by name…" bind:value={q} oninput={onFilter} />
		</div>
		<div class="filters">
			{#each [['', 'All'], ['conservative', 'Conservative'], ['balanced', 'Balanced'], ['aggressive', 'Aggressive']] as [val, label]}
				<button
					class="fbtn"
					class:on={riskFilter === val}
					onclick={() => {
						riskFilter = val;
						load();
					}}>{label}</button
				>
			{/each}
		</div>
	</div>

	<div class="card full">
		{#if loading}
			<div class="loading">Loading clients…</div>
		{:else if error}
			<div class="errbox">Couldn't reach the API — {error}</div>
		{:else}
			<p class="h2sub" style="margin-top:2px">
				{clients.length} client{clients.length === 1 ? '' : 's'}
			</p>
			<div class="h2rule"></div>
			<table>
				<thead>
					<tr>
						{@render sortHead('name', 'Client')}
						{@render sortHead('risk_profile', 'Profile')}
						{@render sortHead('age', 'Age', true)}
						{@render sortHead('fund_count', 'Funds', true)}
						{@render sortHead('goal_count', 'Goals', true)}
						{@render sortHead('portfolio_value', 'Portfolio', true)}
					</tr>
				</thead>
				<tbody>
					{#each sorted as c, i (c.id)}
						<tr
							class="rowlink"
							in:fly|global={{ y: 6, duration: 220, delay: Math.min(i, 28) * 24 }}
							onclick={() => goto(`/clients/${c.id}`)}
						>
							<td class="name">{c.name}</td>
							<td><RiskPill profile={c.risk_profile} /></td>
							<td class="r num">{c.age ?? '—'}</td>
							<td class="r num">{c.fund_count}</td>
							<td class="r num">{c.goal_count}</td>
							<td class="r num">{inr(c.portfolio_value)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
			{#if clients.length === 0}
				<div class="loading">No clients match.</div>
			{/if}
		{/if}
	</div>
</div>

<style>
	tr.rowlink {
		transition: background-color 120ms ease;
	}
	tr.rowlink:hover td:first-child {
		box-shadow: inset 3px 0 0 var(--brand);
	}
	tr.rowlink:hover td.name {
		color: var(--brand-strong);
	}
	td.name {
		transition: color 120ms ease;
	}

	th.sortable {
		cursor: pointer;
		user-select: none;
	}
	th.sortable:hover {
		color: var(--ink);
	}
	th.active {
		color: var(--brand);
	}
	.thlabel {
		display: inline-flex;
		align-items: center;
		gap: 3px;
	}
	th.r .thlabel {
		justify-content: flex-end;
		width: 100%;
	}
	.sortidle {
		opacity: 0;
		transition: opacity 120ms;
		display: inline-flex;
	}
	th.sortable:hover .sortidle {
		opacity: 0.5;
	}

	.controls {
		display: flex;
		gap: 14px;
		align-items: center;
		flex-wrap: wrap;
		margin-bottom: 22px;
	}
	.searchbox {
		flex: 1;
		min-width: 220px;
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 0 14px;
		background: var(--paper);
		border: 1.5px solid var(--primary-800);
		box-shadow: var(--shadow-stamp-sm);
		color: var(--mut);
	}
	.searchbox:focus-within {
		box-shadow: var(--shadow-stamp);
	}
	.search {
		flex: 1;
		font-family: var(--font-sans);
		font-size: 14px;
		padding: 11px 0;
		background: transparent;
		border: none;
		color: var(--ink);
	}
	.search:focus {
		outline: none;
	}
	.filters {
		display: flex;
		gap: 2px;
		background: var(--paper);
		border: 1.5px solid var(--primary-800);
		box-shadow: var(--shadow-stamp-sm);
		padding: 4px;
	}
	.fbtn {
		font-family: var(--font-sans);
		font-size: 11px;
		font-weight: 600;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		padding: 8px 12px;
		border: none;
		background: transparent;
		color: var(--mut);
		cursor: pointer;
	}
	.fbtn.on {
		background: var(--brand);
		color: var(--on-brand);
	}
	.fbtn:hover:not(.on) {
		background: var(--card-2);
		color: var(--ink);
	}
</style>
