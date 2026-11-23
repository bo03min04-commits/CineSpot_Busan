import rules from '../shared/nearbyCorrections.json';

const compact = (value) => String(value ?? '').toLowerCase().replace(/[^0-9a-z가-힣]/g, '');
const street = (value) => {
  const text = String(value ?? '').replace('부산광역시', '부산');
  const match = text.match(/^(.+?(?:대로|로|길))\s*(\d+)(?:\s*-\s*(\d+))?/);
  return match ? `${compact(match[1])}:${Number(match[2])}:${Number(match[3] || 0)}` : compact(text);
};

export function correctNearbyPlace(place) {
  if (!place) return null;
  const rule = rules.find((item) => item.names.some((name) => compact(name) === compact(place.name))
    && street(item.address) === street(place.address));
  if (rule?.action === 'exclude') return null;
  return rule?.action === 'rename' ? { ...place, name: rule.name } : place;
}
