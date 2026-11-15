const labels = {LIGHTING:'조명', POLE:'등주', SOLAR:'태양광', ELECTRICAL:'전기', OTHER:'기타', UNCLASSIFIED:'미분류'};

/** Display labels only; stored category codes remain unchanged. */
export function budgetCategoryLabel(category) {
 return labels[category] || category || '미분류';
}
