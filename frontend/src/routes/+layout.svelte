<script>
	import './layout.css';
	import favicon from '$lib/assets/favicon.svg';
	import TopBar from '$lib/components/TopBar.svelte';

	let { children } = $props();
</script>

<svelte:head><link rel="icon" href={favicon} /><title>AdvisorOS</title></svelte:head>

<TopBar />
{@render children()}
