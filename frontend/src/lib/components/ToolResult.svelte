<script>
	// Renders one Copilot tool call as a labelled result card — the "visible tool-call
	// trace" from IMPLEMENTATION.md §9. Each of the six tools gets its own body; the raw
	// JSON is always available behind a details disclosure for the demo.
	import { goto } from '$app/navigation';
	import { inr, inrFull, pct, catLabel } from '$lib/api.js';
	import ProjectionChart from './ProjectionChart.svelte';
	import {
		Search,
		FileText,
		FlaskConical,
		Siren,
		ListOrdered,
		Target,
		Database,
		ReceiptText,
		TrendingUp,
		TrendingDown,
		ArrowRight,
		Check,
		AlertTriangle,
		LoaderCircle
	} from '@lucide/svelte';

	let { entry, oncommit, index = null, pending = false } = $props();

	const result = $derived(entry?.result ?? {});
	const isError = $derived(!pending && result && typeof result === 'object' && 'error' in result);
	const num = $derived(index != null ? String(index + 1).padStart(2, '0') : null);

	const META = {
		query_book: { label: 'query_book', icon: Search },
		get_client_brief: { label: 'get_client_brief', icon: FileText },
		run_whatif: { label: 'run_whatif', icon: FlaskConical },
		project_portfolio: { label: 'project_portfolio', icon: TrendingUp },
		stress_book: { label: 'stress_book', icon: Siren },
		rank_book: { label: 'rank_book', icon: ListOrdered },
		rank_goal_shortfalls: { label: 'rank_goal_shortfalls', icon: Target },
		run_sql: { label: 'run_sql', icon: Database },
		add_transactions: { label: 'add_transactions', icon: ReceiptText }
	};
	const toolLabel = $derived(META[entry.tool]?.label ?? entry.tool);
	const ToolIcon = $derived(META[entry.tool]?.icon ?? FlaskConical);

	function statusClass(s) {
		return s === 'breach' ? 'crit' : s === 'watch' ? 'warn' : 'good';
	}
	function profileClass(p) {
		return p === 'aggressive' ? 'crit' : p === 'conservative' ? 'good' : 'warn';
	}
	// run_sql cells: cap decimals at 2dp so long floats don't blow out the table.
	function fmtCell(v) {
		if (typeof v === 'number' && !Number.isInteger(v)) return v.toFixed(2);
		return v ?? '—';
	}
	// Rows the advisor can commit: matched proposals mapped to the ledger shape.
	const committable = $derived(
		(result?.proposed ?? [])
			.filter((r) => r.matched && r.fund_id && r.units && r.nav)
			.map((r) => ({
				fund_id: r.fund_id,
				type: r.type,
				date: r.date,
				units: r.units,
				nav: r.nav,
				amount: r.amount
			}))
	);
</script>

<div class="tool" class:pending>
	<div class="toolhead">
		{#if num}<span class="tnum">№{num}</span>{/if}
		<span class="tchip"><ToolIcon size={13} strokeWidth={2} />{toolLabel}</span>
		{#if entry.args && Object.keys(entry.args).length}
			<span class="targs">{JSON.stringify(entry.args)}</span>
		{/if}
		{#if pending}
			<span class="ttime pend"><LoaderCircle size={12} strokeWidth={2} class="spin" />running</span>
		{:else if result?.elapsed_ms != null}
			<span class="ttime">{result.backend?.includes('GPU') ? 'GPU' : 'CPU'} · {result.elapsed_ms} ms</span>
		{/if}
	</div>

	{#if pending}
		<p class="cap">Consulting {toolLabel}…</p>
	{:else if isError}
		<p class="err"><AlertTriangle size={14} /> {result.error}</p>

	<!-- ── rank_book / query_book: a client table ────────────────────────────── -->
	{:else if entry.tool === 'rank_book'}
		{@const list = result.call_list ?? []}
		<p class="cap">{result.count} clients ranked by suitability mismatch</p>
		<div class="tablewrap">
			<table>
				<thead><tr><th>Client</th><th>Profile</th><th class="r">Downside</th><th class="r">Mismatch</th><th>Worst goal</th></tr></thead>
				<tbody>
					{#each list.slice(0, 8) as c}
						<tr class="rowlink" onclick={() => goto(`/clients/${c.client_id}`)}>
							<td class="name">{c.name}</td>
							<td><span class="pill {profileClass(c.risk_profile)}">{c.risk_profile}</span></td>
							<td class="r num">{pct(c.simulated_dd, 0)}</td>
							<td class="r num"><span class="pill {statusClass(c.status)}">{c.suitability_mismatch > 0 ? '+' : ''}{pct(c.suitability_mismatch, 0)}</span></td>
							<td class="dimc">{c.worst_goal ?? '—'}{#if c.worst_goal_prob != null} · {pct(c.worst_goal_prob, 0)}{/if}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

	{:else if entry.tool === 'query_book'}
		{@const list = result.clients ?? []}
		{@const showAmc = !!result.criteria?.over_concentrated_amc}
		<p class="cap">
			{result.count} match{result.count === 1 ? '' : 'es'}{#if result.truncated} · showing {result.returned}{/if}{#if result.criteria && Object.keys(result.criteria).length} · {Object.entries(result.criteria).map(([k, v]) => `${k}: ${v}`).join(', ')}{/if}
		</p>
		<div class="tablewrap">
			<table>
				<thead><tr><th>Client</th><th>Profile</th><th class="r">Value</th><th>Top exposure</th>{#if showAmc}<th>Top AMC</th>{/if}<th class="r">Mismatch</th></tr></thead>
				<tbody>
					{#each list.slice(0, 8) as c}
						<tr class="rowlink" onclick={() => goto(`/clients/${c.client_id}`)}>
							<td class="name">{c.name}</td>
							<td><span class="pill {profileClass(c.risk_profile)}">{c.risk_profile}</span></td>
							<td class="r num">{inr(c.portfolio_value)}</td>
							<td class="dimc">{c.top_category ? catLabel(c.top_category) : '—'}{#if c.top_weight} · {pct(c.top_weight, 0)}{/if}</td>
							{#if showAmc}<td class="dimc">{c.top_amc ?? '—'}{#if c.top_amc_weight} · {pct(c.top_amc_weight, 0)}{/if}</td>{/if}
							<td class="r num">{c.suitability_mismatch != null ? (c.suitability_mismatch > 0 ? '+' : '') + pct(c.suitability_mismatch, 0) : '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

	<!-- ── rank_goal_shortfalls: off-track goals ranked by ₹ gap ─────────────── -->
	{:else if entry.tool === 'rank_goal_shortfalls'}
		{@const list = result.ranked ?? []}
		<p class="cap">{result.count} off-track goal{result.count === 1 ? '' : 's'} ranked by expected shortfall</p>
		<div class="tablewrap">
			<table>
				<thead><tr><th>Client</th><th>Goal</th><th class="r">Target</th><th class="r">Success</th><th class="r">Shortfall</th></tr></thead>
				<tbody>
					{#each list.slice(0, 8) as g}
						<tr class="rowlink" onclick={() => goto(`/clients/${g.client_id}`)}>
							<td class="name">{g.name}</td>
							<td class="dimc">{g.goal_name ?? '—'}</td>
							<td class="r num">{inr(g.target_amount)}</td>
							<td class="r num">{pct(g.success_prob, 0)}</td>
							<td class="r num cr-text">{inr(g.shortfall_expected)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

	<!-- ── get_client_brief ──────────────────────────────────────────────────── -->
	{:else if entry.tool === 'get_client_brief'}
		{@const briefId = result.client_id ?? entry.args?.client_id}
		<div class="briefhead">
			<div>
				{#if briefId != null}
					<button class="bname blink" onclick={() => goto(`/clients/${briefId}`)}>{result.name}</button>
				{:else}
					<div class="bname">{result.name}</div>
				{/if}
				<div class="cap">
					{#if result.age}{result.age} yrs · {/if}<span class="pill {profileClass(result.risk_profile)}">{result.risk_profile}</span> · {inr(result.portfolio_value)}
				</div>
			</div>
		</div>
		{#if result.monthly_sip != null}
			<div class="sipline">
				<span class="siplab">SIP</span>
				<span class="sipval">{inrFull(result.monthly_sip)}<span class="sipper">/mo</span></span>
				{#if result.sip_count}<span class="sipsub">across {result.sip_count} fund{result.sip_count === 1 ? '' : 's'}</span>{/if}
				{#if result.sips?.length}
					<span class="sipfunds">
						{#each result.sips.slice(0, 3) as s}
							<span class="sipchip">{catLabel(s.category)} {inr(s.monthly_amount)}</span>
						{/each}
					</span>
				{/if}
			</div>
		{/if}
		{#if result.scored}
			{#each result.goals ?? [] as g}
				<div class="goalrow">
					<div class="gtop"><span class="gname">{g.name}</span><span class="gp num">{pct(g.success_prob, 0)}</span></div>
					<div class="bar"><div class="fill {g.on_track ? 'good' : 'warn'}" style="width:{Math.min(100, (g.success_prob ?? 0) * 100)}%"></div></div>
					<div class="cap">Target {inr(g.target_amount)} · {g.on_track ? 'on track' : 'off track'}</div>
				</div>
			{/each}
			{#if result.risk}
				<div class="riskrow">
					<div class="rk"><span class="rlab">Worst-year loss</span><span class="rval {result.risk.over_exposed ? 'crit' : ''}">{pct(result.risk.max_drawdown, 0)}</span></div>
					<div class="rk"><span class="rlab">Tolerable</span><span class="rval">{pct(result.risk.tolerable_dd, 0)}</span></div>
					<div class="rk"><span class="rlab">Suitability</span><span class="rval {result.risk.over_exposed ? 'crit' : 'good'}">{result.risk.suitability_mismatch > 0 ? '+' : ''}{pct(result.risk.suitability_mismatch, 0)}</span></div>
				</div>
			{/if}
			{#if result.flags?.length}<div class="flags">{#each result.flags as f}<span class="pill neutral">{f.replace(/_/g, ' ')}</span>{/each}</div>{/if}
		{:else}
			<p class="cap">{result.note}</p>
		{/if}

	<!-- ── run_whatif: before/after ──────────────────────────────────────────── -->
	{:else if entry.tool === 'run_whatif'}
		{#if result.levers?.length}<p class="cap">Change: {result.levers.join(' · ')}</p>{/if}
		{#each result.goals ?? [] as g}
			<div class="goalrow">
				<div class="gtop">
					<span class="gname">{g.name}</span>
					<span class="delta {g.prob_delta >= 0 ? 'good' : 'crit'}">
						{#if g.prob_delta >= 0}<TrendingUp size={13} />{:else}<TrendingDown size={13} />{/if}
						{g.prob_delta > 0 ? '+' : ''}{pct(g.prob_delta, 0)}
					</span>
				</div>
				<div class="baa">
					<span class="num was">{pct(g.before.success_prob, 0)}</span>
					<ArrowRight size={13} />
					<span class="num now {g.prob_delta >= 0 ? 'good' : 'crit'}">{pct(g.after.success_prob, 0)}</span>
				</div>
				<div class="dualbar">
					<div class="bar sm"><div class="fill mut" style="width:{Math.min(100, g.before.success_prob * 100)}%"></div></div>
					<div class="bar sm"><div class="fill {g.prob_delta >= 0 ? 'good' : 'crit'}" style="width:{Math.min(100, g.after.success_prob * 100)}%"></div></div>
				</div>
			</div>
		{/each}
		{#if result.portfolio}
			{@const p = result.portfolio}
			<div class="riskrow">
				<div class="rk"><span class="rlab">Worst-year loss</span><span class="rval">{pct(p.before.max_drawdown, 0)} <ArrowRight size={11} /> <b class={p.after.max_drawdown <= p.before.max_drawdown ? 'good' : 'crit'}>{pct(p.after.max_drawdown, 0)}</b></span></div>
				<div class="rk"><span class="rlab">Tolerable</span><span class="rval">{pct(p.tolerable_dd, 0)}</span></div>
			</div>
		{/if}

	<!-- ── project_portfolio: history + projected P5/P50/P90 fan chart ──────────── -->
	{:else if entry.tool === 'project_portfolio'}
		{@const h = result.headline ?? {}}
		{@const p50 = result.projection?.p50 ?? []}
		{@const p5 = result.projection?.p5 ?? []}
		{@const p90 = result.projection?.p90 ?? []}
		{@const current = h.current_value ?? result.current_value}
		{@const median = h.median_at_horizon ?? p50[p50.length - 1]}
		{@const worst = h.worst_at_horizon ?? p5[p5.length - 1]}
		{@const best = h.best_at_horizon ?? p90[p90.length - 1]}
		{@const years = h.horizon_years ?? result.horizon_years}
		{#if result.levers?.length}<p class="cap">Change: {result.levers.join(' · ')}</p>{/if}
		<div class="stresshead">
			<div class="kpimini"><div class="kv">{inr(current)}</div><div class="kl">value today</div></div>
			{#if median != null}
				<div class="kpimini"><div class="kv">{inr(median)}</div><div class="kl">median in {years} yrs</div></div>
				<span class="cap">range {inr(worst)} – {inr(best)}</span>
			{/if}
		</div>
		<ProjectionChart history={result.history ?? []} projection={result.projection} />

	<!-- ── stress_book: breaches on a crash · gainers on a rally ─────────────── -->
	{:else if entry.tool === 'stress_book'}
		{@const up = result.direction === 'up'}
		{@const book = result.book ?? {}}
		<div class="stresshead">
			{#if up}
				<div class="kpimini good">
					<div class="kv in-text">{book.expected_pct > 0 ? '+' : ''}{pct(book.expected_pct, 1)}</div>
					<div class="kl">expected book move</div>
				</div>
				<div class="kpimini"><div class="kv in-text">{inr(book.expected_amount)}</div><div class="kl">expected gain</div></div>
			{:else}
				<div class="kpimini crit"><div class="kv">{result.breaches}</div><div class="kl">breach tolerance</div></div>
				<div class="kpimini"><div class="kv">{result.clients_evaluated}</div><div class="kl">clients hit</div></div>
			{/if}
			<div class="shockdesc">
				{#each Object.entries(result.shock ?? {}) as [cat, d]}<span class="pill {d < 0 ? 'crit' : 'good'}">{catLabel(cat)} {d > 0 ? '+' : ''}{(d * 100).toFixed(0)}%</span>{/each}
				{#if result.horizon_months}<span class="cap">over {result.horizon_months} mo · {result.mode}</span>{/if}
			</div>
		</div>
		{#if book.value != null}
			<div class="rangeline">
				<span class="rl-lab">Book range</span>
				<span class="rl-bad">{pct(book.downside_pct, 1)}</span>
				<span class="rl-mid">expected {book.expected_pct > 0 ? '+' : ''}{pct(book.expected_pct, 1)}</span>
				<span class="rl-good">+{pct(book.upside_pct, 1)}</span>
			</div>
		{/if}
		{#if up}
			{#if result.movers?.length}
				<p class="cap">Biggest beneficiaries — expected ₹ gain across the book</p>
				<div class="tablewrap">
					<table>
						<thead><tr><th>Client</th><th>Profile</th><th class="r">Expected</th><th class="r">Gain</th><th class="r">Best case</th></tr></thead>
						<tbody>
							{#each result.movers.slice(0, 8) as c}
								<tr class="rowlink" onclick={() => goto(`/clients/${c.client_id}`)}>
									<td class="name">{c.name}</td>
									<td><span class="pill {profileClass(c.risk_profile)}">{c.risk_profile}</span></td>
									<td class="r num in-text">{c.expected_pct > 0 ? '+' : ''}{pct(c.expected_pct, 1)}</td>
									<td class="r num in-text">{inr(c.expected_amount)}</td>
									<td class="r num">+{pct(c.upside_pct, 0)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		{:else if result.ranked?.length}
			<div class="tablewrap">
				<table>
					<thead><tr><th>Client</th><th>Profile</th><th class="r">Loss</th><th class="r">Tolerable</th><th class="r">Over by</th></tr></thead>
					<tbody>
						{#each result.ranked.slice(0, 8) as c}
							<tr class="rowlink" onclick={() => goto(`/clients/${c.client_id}`)}>
								<td class="name">{c.name}</td>
								<td><span class="pill {profileClass(c.risk_profile)}">{c.risk_profile}</span></td>
								<td class="r num cr-text">{pct(c.loss, 0)}</td>
								<td class="r num">{pct(c.tolerable, 0)}</td>
								<td class="r num"><span class="pill crit">+{pct(c.severity, 0)}</span></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else}
			<p class="cap">No clients breach tolerance under this shock.</p>
		{/if}

	<!-- ── add_transactions: proposed rows + commit ──────────────────────────── -->
	{:else if entry.tool === 'add_transactions'}
		<p class="cap">Parsed {result.proposed?.length ?? 0} · {result.ready_to_commit} ready · {result.needs_review} need review — <b>nothing written yet</b></p>
		<div class="tablewrap">
			<table>
				<thead><tr><th>Type</th><th>Fund</th><th class="r">Amount</th><th class="r">NAV</th><th class="r">Units</th><th></th></tr></thead>
				<tbody>
					{#each result.proposed ?? [] as r}
						<tr>
							<td><span class="pill {r.type === 'buy' ? 'good' : 'crit'}">{r.type}</span></td>
							<td class="name">{r.matched ? r.fund_name : r.fund_query}<div class="cap">{r.matched ? catLabel(r.category) : (r.note ?? 'unmatched')}</div></td>
							<td class="r num">{inrFull(r.amount)}</td>
							<td class="r num">{r.nav ?? '—'}</td>
							<td class="r num">{r.units ?? '—'}</td>
							<td class="r">{#if r.matched}<Check size={15} class="okmark" />{:else}<AlertTriangle size={15} class="warnmark" />{/if}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if committable.length}
			<button class="commit" onclick={() => oncommit?.(result.client_id, committable)}>
				<Check size={14} /> Commit {committable.length} to ledger
			</button>
		{/if}

	<!-- ── run_sql: ad-hoc read-only query result ────────────────────────────── -->
	{:else if entry.tool === 'run_sql'}
		<details class="sqlbox">
			<summary><Database size={12} strokeWidth={2} /> SQL query</summary>
			<pre class="raw">{result.query}</pre>
		</details>
		{#if result.columns?.length}
			<p class="cap">
				{result.row_count} row{result.row_count === 1 ? '' : 's'}{#if result.truncated} · truncated to {result.row_count}{/if}
			</p>
			<div class="tablewrap">
				<table>
					<thead><tr>{#each result.columns as col}<th>{col}</th>{/each}</tr></thead>
					<tbody>
						{#each result.rows.slice(0, 20) as row}
							<tr>{#each result.columns as col}<td class="num">{fmtCell(row[col])}</td>{/each}</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else}
			<p class="cap">No rows returned.</p>
		{/if}
	{:else}
		<pre class="raw">{JSON.stringify(result, null, 2)}</pre>
	{/if}
</div>

<style>
	.tool {
		border: 1.5px solid var(--primary-800);
		background: var(--card);
		box-shadow: var(--shadow-stamp-sm);
		padding: 12px 14px 14px;
		margin: 10px 0;
		animation: cardIn 0.32s var(--ease-out) both;
	}
	@keyframes cardIn {
		from {
			opacity: 0;
			transform: translateY(8px);
		}
		to {
			opacity: 1;
			transform: none;
		}
	}
	@keyframes growx {
		from {
			transform: scaleX(0);
		}
		to {
			transform: scaleX(1);
		}
	}
	.toolhead {
		display: flex;
		align-items: center;
		gap: 10px;
		flex-wrap: wrap;
		margin-bottom: 10px;
	}
	.tnum {
		font-family: var(--font-serif);
		font-style: italic;
		font-weight: 700;
		font-size: 13px;
		color: var(--mut);
		flex: none;
	}
	.tchip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-family: var(--font-sans);
		font-size: 11px;
		font-weight: 700;
		letter-spacing: 0.06em;
		background: var(--brand);
		color: var(--on-brand);
		padding: 4px 9px;
	}
	.tchip :global(svg) {
		flex: none;
	}
	.targs {
		font-family: var(--font-sans);
		font-size: 11px;
		color: var(--mut);
		font-variant-numeric: tabular-nums;
		overflow: hidden;
		text-overflow: ellipsis;
		max-width: 340px;
		white-space: nowrap;
	}
	.ttime {
		margin-left: auto;
		font-size: 10.5px;
		font-weight: 700;
		letter-spacing: 0.08em;
		color: var(--inflow);
		text-transform: uppercase;
	}
	.ttime.pend {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		color: var(--mut);
	}
	.ttime.pend :global(.spin) {
		animation: spin 0.9s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	.tool.pending {
		opacity: 0.7;
	}
	.cap {
		font-size: 12px;
		color: var(--mut);
		margin: 2px 0 8px;
	}
	.cap b {
		color: var(--ink);
	}
	.tablewrap {
		overflow-x: auto;
	}
	.tool table {
		font-size: 13px;
	}
	.tool th {
		padding: 0 8px 8px;
	}
	.tool td {
		padding: 7px 8px;
		font-size: 13px;
	}
	.tool td.name {
		font-size: 13.5px;
	}
	tr.rowlink {
		transition: background-color 120ms ease;
	}
	tr.rowlink:hover td.name {
		color: var(--brand-strong);
	}
	.dimc {
		color: var(--mut);
		font-size: 12px;
	}
	.cr-text {
		color: var(--outflow);
		font-weight: 700;
	}
	.in-text {
		color: var(--inflow);
		font-weight: 700;
	}
	.err {
		color: var(--secondary-700);
		font-size: 13px;
		display: flex;
		gap: 6px;
		align-items: center;
	}
	/* brief + whatif goal rows */
	.briefhead {
		margin-bottom: 10px;
	}
	.bname {
		font-family: var(--font-serif);
		font-weight: 700;
		font-size: 19px;
		color: var(--ink);
	}
	button.blink {
		background: none;
		border: none;
		padding: 0;
		cursor: pointer;
		text-align: left;
		text-decoration: underline;
		text-decoration-style: dotted;
		text-underline-offset: 3px;
		transition: color 120ms ease;
	}
	button.blink:hover {
		color: var(--brand-strong);
		text-decoration-style: solid;
	}
	.sipline {
		display: flex;
		align-items: baseline;
		flex-wrap: wrap;
		gap: 8px;
		padding: 8px 10px;
		margin-bottom: 4px;
		background: var(--card-2);
		border: 1px solid var(--primary-300);
	}
	.siplab {
		font-family: var(--font-sans);
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--mut);
	}
	.sipval {
		font-weight: 700;
		font-size: 15px;
		color: var(--inflow);
		font-variant-numeric: tabular-nums;
	}
	.sipper {
		font-size: 11px;
		font-weight: 600;
		color: var(--mut);
	}
	.sipsub {
		font-size: 12px;
		color: var(--mut);
	}
	.sipfunds {
		display: inline-flex;
		gap: 5px;
		flex-wrap: wrap;
		margin-left: auto;
	}
	.sipchip {
		font-size: 10.5px;
		color: var(--ink-2);
		background: var(--paper);
		border: 1px solid var(--primary-300);
		padding: 2px 7px;
		font-variant-numeric: tabular-nums;
	}
	.goalrow {
		padding: 8px 0;
		border-top: 1px solid var(--primary-200);
	}
	.gtop {
		display: flex;
		justify-content: space-between;
		align-items: center;
	}
	.gname {
		font-weight: 600;
		font-size: 13.5px;
		color: var(--ink-2);
	}
	.gp {
		font-weight: 700;
		font-variant-numeric: tabular-nums;
	}
	.bar {
		height: 8px;
		background: var(--card-2);
		border: 1px solid var(--primary-300);
		margin: 5px 0 3px;
		overflow: hidden;
	}
	.bar.sm {
		height: 6px;
		margin: 3px 0;
	}
	.fill {
		height: 100%;
		background: var(--brand);
		transform-origin: left center;
		animation: growx 0.55s var(--ease-out) both;
	}
	.fill.good {
		background: var(--inflow);
	}
	.fill.warn {
		background: var(--primary-600);
	}
	.fill.crit {
		background: var(--outflow);
	}
	.fill.mut {
		background: var(--primary-300);
	}
	.delta {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		font-weight: 700;
		font-size: 13px;
		font-variant-numeric: tabular-nums;
	}
	.delta.good {
		color: var(--inflow);
	}
	.delta.crit {
		color: var(--outflow);
	}
	.baa {
		display: flex;
		align-items: center;
		gap: 8px;
		margin: 3px 0;
		font-variant-numeric: tabular-nums;
	}
	.baa .was {
		color: var(--mut);
	}
	.baa .now {
		font-weight: 700;
	}
	.baa .now.good {
		color: var(--inflow);
	}
	.baa .now.crit {
		color: var(--outflow);
	}
	.dualbar {
		margin-top: 2px;
	}
	.riskrow {
		display: flex;
		gap: 20px;
		flex-wrap: wrap;
		margin-top: 10px;
		padding-top: 10px;
		border-top: 1px solid var(--primary-200);
	}
	.rk {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.rlab {
		font-size: 10px;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--mut);
		font-weight: 600;
	}
	.rval {
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		display: inline-flex;
		align-items: center;
		gap: 5px;
	}
	.rval.crit {
		color: var(--outflow);
	}
	.rval.good {
		color: var(--inflow);
	}
	.rval b.good {
		color: var(--inflow);
	}
	.rval b.crit {
		color: var(--outflow);
	}
	.flags {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
		margin-top: 10px;
	}
	/* stress */
	.stresshead {
		display: flex;
		gap: 16px;
		align-items: center;
		flex-wrap: wrap;
		margin-bottom: 10px;
	}
	.kpimini {
		border-left: 4px solid var(--primary-900);
		padding: 2px 12px;
	}
	.kpimini.crit {
		border-left-color: var(--outflow);
	}
	.kpimini.good {
		border-left-color: var(--inflow);
	}
	.rangeline {
		display: flex;
		align-items: center;
		gap: 10px;
		flex-wrap: wrap;
		font-size: 12px;
		font-variant-numeric: tabular-nums;
		padding: 7px 10px;
		margin-bottom: 10px;
		background: var(--card-2);
		border: 1px solid var(--primary-200);
	}
	.rl-lab {
		font-size: 10px;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--mut);
		font-weight: 700;
	}
	.rl-bad {
		color: var(--outflow);
		font-weight: 700;
	}
	.rl-good {
		color: var(--inflow);
		font-weight: 700;
	}
	.rl-mid {
		color: var(--ink-2);
		font-weight: 600;
	}
	.kpimini .kv {
		font-family: var(--font-serif);
		font-size: 30px;
		font-weight: 800;
		line-height: 1;
	}
	.kpimini .kl {
		font-size: 10px;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--mut);
		font-weight: 600;
	}
	.shockdesc {
		display: flex;
		gap: 6px;
		align-items: center;
		flex-wrap: wrap;
	}
	.commit {
		margin-top: 12px;
		display: inline-flex;
		align-items: center;
		gap: 7px;
		font-family: var(--font-sans);
		font-size: 12px;
		font-weight: 700;
		letter-spacing: 0.04em;
		background: var(--inflow);
		color: #fff;
		border: 1.5px solid var(--primary-900);
		box-shadow: var(--shadow-stamp-sm);
		padding: 9px 14px;
		cursor: pointer;
		transition:
			transform 0.12s var(--ease-out),
			box-shadow 0.12s var(--ease-out);
	}
	.commit:hover {
		transform: translate(-1px, -1px);
		box-shadow: var(--shadow-stamp);
	}
	.commit:active {
		transform: translate(1px, 1px);
		box-shadow: var(--shadow-stamp-sm);
	}
	.raw {
		font-size: 11px;
		background: var(--card-2);
		padding: 10px;
		overflow-x: auto;
		border: 1px solid var(--primary-200);
	}
	.sqlbox {
		margin-bottom: 8px;
	}
	.sqlbox summary {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		cursor: pointer;
		font-family: var(--font-sans);
		font-size: 10.5px;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--mut);
		user-select: none;
		list-style: none;
	}
	.sqlbox summary::-webkit-details-marker {
		display: none;
	}
	.sqlbox summary :global(svg) {
		flex: none;
	}
	.sqlbox summary:hover {
		color: var(--ink-2);
	}
	.sqlbox[open] summary {
		margin-bottom: 6px;
	}
	:global(.okmark) {
		color: var(--inflow);
	}
	:global(.warnmark) {
		color: var(--primary-600);
	}
	@media (prefers-reduced-motion: reduce) {
		.tool,
		.fill,
		.ttime.pend :global(.spin) {
			animation: none !important;
		}
	}
</style>
