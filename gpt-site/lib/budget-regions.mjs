import {canonicalRegion} from './g2b-domain.mjs';
import {regionHistoryMembers} from './read-filters.mjs';

// Read aliases only: preserve provider region/institution codes and source rows.
const aliases = [
 ['서울특별시','서울','서울시'], ['부산광역시','부산'], ['대구광역시','대구'],
 ['인천광역시','인천'], ['광주광역시','광주'], ['대전광역시','대전'],
 ['울산광역시','울산'], ['세종특별자치시','세종'], ['경기도','경기'],
 ['강원특별자치도','강원도','강원'], ['충청북도','충북'], ['충청남도','충남'],
 ['전북특별자치도','전라북도','전북'], ['전라남도','전남'],
 ['경상북도','경북'], ['경상남도','경남'], ['제주특별자치도','제주도','제주'],
 ['전남광주통합특별시','전남광주'],
];
export function budgetRegionMembers(value) {
  return [...new Set(regionHistoryMembers(value).flatMap(region => aliases.find(group => group.includes(region)) || [region]))];
}
export function budgetRegionWhere(value, where, args, column = 'region') {
  if (!value) return;
  const values = budgetRegionMembers(value);
  where.push(`${column} IN (${values.map(() => '?').join(',')})`);
  args.push(...values);
}
export function budgetCoverage(rows, service) {
  const services = ['QWGJK','AIDFA'].map(source => {
    const selected = rows.filter(row => row.service === source);
    const regions = [...new Set(selected.map(row => canonicalRegion(row.region) || row.region).filter(Boolean))].sort();
    return {service: source, rows: selected.reduce((sum,row) => sum + Number(row.rows), 0), regions, regionCount: regions.length};
  });
  const names = [...new Set(services.flatMap(source => source.regions))].sort();
  return {services, regions: names, choices: names.map(region => {
    const members = budgetRegionMembers(region);
    const current = rows.filter(row => row.service === service && members.includes(row.region));
    return {region, rows: current.reduce((sum,row) => sum + Number(row.rows), 0)};
  }), sourceCompletenessVerified: false, scope: 'STORED_CURRENT_FISCAL_YEAR'};
}
