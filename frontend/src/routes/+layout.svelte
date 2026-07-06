<script>
	import './layout.css';
	import favicon from '$lib/assets/favicon.svg';
	import TopBar from '$lib/components/TopBar.svelte';
	import { page } from '$app/state';

	let { children } = $props();

	// The shared client one-pager is meant for someone outside the app (no login,
	// no internal nav) — keep it clean of the advisor's TopBar.
	const isPublicShare = $derived(page.url.pathname.startsWith('/share/'));
</script>

<svelte:head><link rel="icon" href={favicon} /><title>AdvisorOS</title></svelte:head>

{#if !isPublicShare}
	<TopBar />
{/if}
{@render children()}
