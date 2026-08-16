export function scoreColor(bhi) {
  if (bhi === null || bhi === undefined) return '#687681';
  if (bhi < 20) return '#ff6b61';
  if (bhi < 40) return '#ff9a61';
  if (bhi < 60) return '#e8d96e';
  if (bhi < 80) return '#83e88e';
  return '#5ff2a8';
}

export function evidenceTierNote(coverage) {
  if (coverage >= 0.8) return { label: 'Strong evidence', tone: 'green' };
  if (coverage >= 0.4) return { label: 'Partial evidence', tone: 'blue' };
  return { label: 'Insufficient evidence', tone: 'neutral' };
}
