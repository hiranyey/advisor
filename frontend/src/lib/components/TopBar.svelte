<script>
	import { page } from '$app/state';
	import { Sparkles, Radar, Users } from '@lucide/svelte';

	// Three tabs for now (shareable client docs deferred). Active state matches by
	// path prefix so /clients/[id] keeps the Clients tab lit.
	const tabs = [
		{ href: '/advisor', label: 'AI Advisor', icon: Sparkles, match: (p) => p.startsWith('/advisor') },
		{ href: '/', label: 'Risk Radar', icon: Radar, match: (p) => p === '/' || p.startsWith('/radar') },
		{ href: '/clients', label: 'Clients', icon: Users, match: (p) => p.startsWith('/clients') }
	];

	const path = $derived(page.url.pathname);
</script>

<div class="topbar">
	<nav class="nav">
		{#each tabs as tab}
			{@const Icon = tab.icon}
			<a href={tab.href} class:on={tab.match(path)}>
				<Icon size={15} strokeWidth={1.8} />
				<span>{tab.label}</span>
			</a>
		{/each}
	</nav>
</div>

<style>
	.topbar {
		position: sticky;
		top: 0;
		z-index: 20;
		padding: 14px 20px 10px;
		display: flex;
		align-items: center;
		justify-content: center;
		background: linear-gradient(180deg, var(--primary-100) 55%, transparent);
	}
	.nav,
	.userpill {
		background: var(--paper);
		border: 1.5px solid var(--primary-800);
		box-shadow: var(--shadow-stamp-sm);
	}
	.nav {
		display: flex;
		gap: 2px;
		padding: 5px;
	}
	.nav a {
		display: flex;
		align-items: center;
		gap: 7px;
		text-decoration: none;
		color: var(--mut);
		font-family: var(--font-sans);
		font-size: 11px;
		font-weight: 600;
		letter-spacing: 0.16em;
		text-transform: uppercase;
		padding: 9px 13px;
		white-space: nowrap;
	}
	.nav a svg {
		width: 14px;
		height: 14px;
		stroke: currentColor;
		fill: none;
		stroke-width: 1.6;
	}
	.nav a.on {
		background: var(--brand);
		color: var(--on-brand);
	}
	.nav a:hover:not(.on) {
		color: var(--ink);
		background: var(--card-2);
	}
	.userpill {
		position: absolute;
		right: 20px;
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 10px 15px;
		font-family: var(--font-sans);
		font-size: 11px;
		font-weight: 600;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--ink);
	}
	.userpill .sep {
		color: var(--rule);
	}
	.userpill .moon {
		color: var(--brand);
	}
	.userpill .signout {
		color: var(--secondary-700);
	}
	@media (max-width: 1120px) {
		.userpill {
			display: none;
		}
	}
	@media (max-width: 680px) {
		.nav {
			flex-wrap: wrap;
			justify-content: center;
		}
		.nav a span {
			display: none;
		}
		.nav a {
			padding: 9px;
		}
	}
</style>
