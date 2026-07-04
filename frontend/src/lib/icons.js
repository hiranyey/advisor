// Central lucide-icon mappings so categories, risk profiles, and goals render a
// consistent icon everywhere. Each helper returns a lucide component you render
// directly, e.g. `{@const Icon = catIcon(c)} <Icon size={14} />`.
import {
	Flame,
	TrendingUp,
	ChartLine,
	Globe,
	Wallet,
	Landmark,
	TriangleAlert,
	Coins,
	Gem,
	Zap,
	Scale,
	ShieldCheck,
	Layers,
	Boxes,
	GraduationCap,
	LifeBuoy,
	Gift,
	TreePalm,
	House,
	Car,
	HeartPulse,
	Plane,
	Target,
	Shield,
	Circle
} from '@lucide/svelte';

// The 14 engine categories → an icon each.
const CAT_ICONS = {
	high_risk_equity: Flame,
	mid_risk_equity: TrendingUp,
	low_risk_equity: ChartLine,
	international_equity: Globe,
	cash_equivalent: Wallet,
	good_debt: Landmark,
	bad_debt: TriangleAlert,
	gold: Coins,
	silver: Gem,
	aggressive_hybrid: Zap,
	balanced_advantage: Scale,
	conservative_hybrid: ShieldCheck,
	multi_asset: Layers,
	other: Boxes
};

export function catIcon(category) {
	return CAT_ICONS[category] ?? Boxes;
}

// Risk profile → icon.
const RISK_ICONS = {
	conservative: Shield,
	balanced: Scale,
	aggressive: Flame
};

export function riskIcon(profile) {
	return RISK_ICONS[profile] ?? Circle;
}

// Goals are free-text, so match on keywords in the name.
const GOAL_RULES = [
	[/educat|college|school|study/i, GraduationCap],
	[/emergen|contingen|rainy/i, LifeBuoy],
	[/wedding|marriage/i, Gift],
	[/retire|pension/i, TreePalm],
	[/house|home|property|flat|apartment/i, House],
	[/car|vehicle|bike/i, Car],
	[/health|medical|surgery/i, HeartPulse],
	[/travel|trip|vacation|holiday/i, Plane]
];

export function goalIcon(name) {
	if (name) {
		for (const [re, icon] of GOAL_RULES) {
			if (re.test(name)) return icon;
		}
	}
	return Target;
}
