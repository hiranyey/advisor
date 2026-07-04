<script>
	import { riskIcon } from '$lib/icons.js';

	let { profile } = $props();

	const cls = $derived(
		profile === 'aggressive' ? 'crit' : profile === 'balanced' ? 'warn' : 'good'
	);
	const Icon = $derived(riskIcon(profile));
</script>

<span class="pill pillicon {cls}"><Icon size={12} strokeWidth={2} />{profile ?? '—'}</span>
