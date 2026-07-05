<script>
	import { TriangleAlert, CircleCheck, Info, Eye } from '@lucide/svelte';

	// insight: {kind, severity, title, body, client_ids}. clientNames: {id: name}
	let { insight, clientNames = {} } = $props();

	const SEVERITY = {
		critical: { cls: 'crit', Icon: TriangleAlert },
		watch: { cls: 'warn', Icon: Eye },
		good: { cls: 'good', Icon: CircleCheck },
		info: { cls: 'info', Icon: Info }
	};
	const sev = $derived(SEVERITY[insight.severity] ?? SEVERITY.info);

	const names = $derived(
		(insight.client_ids ?? []).map((id) => ({ id, name: clientNames[id] })).filter((c) => c.name)
	);
</script>

<div class="icard {sev.cls}">
	<div class="ihead">
		<span class="iicon"><sev.Icon size={15} strokeWidth={2.1} /></span>
		<span class="ititle">{insight.title}</span>
	</div>
	<p class="ibody">{insight.body}</p>
	{#if names.length}
		<div class="ichips">
			{#each names as c}
				<a href="/clients/{c.id}" class="ichip">{c.name}</a>
			{/each}
		</div>
	{/if}
</div>

<style>
	.icard {
		border: 1px solid var(--rule);
		border-left-width: 3px;
		background: var(--card);
		padding: 12px 14px;
	}
	.icard.crit {
		border-left-color: var(--outflow);
	}
	.icard.warn {
		border-left-color: var(--primary-700);
	}
	.icard.good {
		border-left-color: var(--inflow);
	}
	.icard.info {
		border-left-color: var(--primary-300);
	}
	.ihead {
		display: flex;
		align-items: center;
		gap: 7px;
		margin-bottom: 6px;
	}
	.iicon {
		display: flex;
		flex: none;
	}
	.icard.crit .iicon {
		color: var(--outflow);
	}
	.icard.warn .iicon {
		color: var(--primary-700);
	}
	.icard.good .iicon {
		color: var(--inflow);
	}
	.icard.info .iicon {
		color: var(--faint);
	}
	.ititle {
		font-family: var(--font-serif);
		font-weight: 700;
		font-size: 14.5px;
		color: var(--ink);
	}
	.ibody {
		font-size: 13px;
		line-height: 1.5;
		color: var(--ink-2);
	}
	.ichips {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		margin-top: 9px;
	}
	.ichip {
		font-family: var(--font-sans);
		font-size: 11px;
		font-weight: 600;
		text-decoration: none;
		padding: 3px 8px;
		background: var(--card-2);
		border: 1px solid var(--primary-300);
		color: var(--mut);
		transition:
			color 140ms ease,
			border-color 140ms ease;
	}
	.ichip:hover {
		color: var(--brand-strong);
		border-color: var(--primary-800);
	}
</style>
